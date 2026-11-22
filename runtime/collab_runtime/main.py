"""Agent run logic. The warm-instance service (service.py) calls run() once per invocation.

Flow: exchange the run lease -> read the API key from Secret Manager -> run the harness in an empty workspace
-> report events to the hub. The lease arrives in the request and is used once.
"""

import logging
import os
import shlex
import tempfile
import threading
import time
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

import httpx

from collab_runtime.github import GitHubError, clone_repository, installation_token
from collab_runtime.harness import ClaudeRunner, build_claude_args
from collab_runtime.hub import HubClient, HubError
from collab_runtime.model_proxy import ModelProxy
from collab_runtime.publish import PublishError, configure_push, write_instructions
from collab_runtime.reply_files import (
    HUB_TEXT_LIMIT,
    INSTRUCTIONS,
    clip,
    extract_reply_files,
)

log = logging.getLogger("collab.runtime")

SUPPORTED_HARNESSES = {"claude_code"}

# Timing is logged as milliseconds since the run began. Each run sets its own start when it begins.
_RUN_START: ContextVar[float] = ContextVar("run_start", default=0.0)


def _mark(stage: str) -> None:
    log.info("timing stage=%s ms=%d", stage, round((time.monotonic() - _RUN_START.get()) * 1000))


@dataclass(frozen=True)
class Config:
    hub_base_url: str
    project_id: str
    anthropic_secret_name: str
    claude_model: str
    claude_binary: str
    timeout_seconds: float
    github_key_secret_name: str = "github-app-key"
    heartbeat_seconds: float = 30.0
    github_app_id: str | None = None
    github_installation_id: str | None = None

    @classmethod
    def from_env(cls, env: dict[str, str]) -> "Config":
        return cls(
            hub_base_url=env["HUB_BASE_URL"],
            project_id=env.get("GCP_PROJECT_ID", "natyasutra-agents"),
            anthropic_secret_name=env.get("ANTHROPIC_SECRET_NAME", "anthropic-api-key"),
            claude_model=env.get("CLAUDE_MODEL", "claude-haiku-5-5"),
            claude_binary=env.get("CLAUDE_BINARY", "claude"),
            timeout_seconds=float(env.get("RUN_TIMEOUT_SECONDS", "1500")),
            github_key_secret_name=env.get("GITHUB_KEY_SECRET_NAME", "github-app-key"),
            heartbeat_seconds=float(env.get("HEARTBEAT_SECONDS", "30")),
            github_app_id=env.get("GITHUB_APP_ID") or None,
            github_installation_id=env.get("GITHUB_INSTALLATION_ID") or None,
        )


class Sequencer:
    """Monotonic event numbers. The hub rejects nothing for a repeat, so a retried event is harmless."""

    def __init__(self, hub: HubClient, runtime_token: str) -> None:
        self._hub = hub
        self._token = runtime_token
        self._next = 0
        self._lock = threading.Lock()

    def send(self, event_type: str, **fields: object) -> None:
        # The lock keeps sequence numbers unique and in order when the heartbeat thread also sends.
        with self._lock:
            event = {"sequence": self._next, "type": event_type, **{k: v for k, v in fields.items() if v is not None}}
            self._hub.post_event(runtime_token=self._token, event=event)
            self._next += 1


class Heartbeat:
    """Sends a liveness event on an interval, so the hub can tell a quiet run from a dead one."""

    def __init__(self, sequencer: Sequencer, interval: float, on_finished: Callable[[], None]) -> None:
        self._sequencer = sequencer
        self._interval = interval
        self._on_finished = on_finished
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="heartbeat", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self._sequencer.send("heartbeat")
            except HubError as error:
                if error.code == "run_finished":
                    self._on_finished()  # the hub ended the run: stop the agent, report nothing further
                    return
                log.warning("heartbeat not delivered: %s", error.code)


def run(
    config: Config,
    *,
    hub: HubClient,
    read_secret: Callable[[str, str], str],
    runner: ClaudeRunner,
    prepare_repo: Callable[[str, str, str], bool | None] | None = None,
    write_token: Callable[[str], str] | None = None,
    lease: str,
    execution_name: str,
) -> int:
    """Run one invocation. Returns the process exit code. Every outcome is reported to the hub first."""
    _RUN_START.set(time.monotonic())
    if not lease or not execution_name:
        log.error("missing run lease or execution name")
        return 2

    _mark("start")
    try:
        context = hub.exchange(lease=lease, execution_name=execution_name)
        _mark("exchanged")
    except HubError as error:
        log.error("lease exchange refused: %s", error.code)
        return 3  # no runtime token exists, so the run cannot be reported; the hub's run timeout handles it

    sequencer = Sequencer(hub, context["runtime_token"])
    finished = threading.Event()

    def stop_agent() -> None:
        finished.set()
        runner.terminate()

    heartbeat = Heartbeat(sequencer, config.heartbeat_seconds, on_finished=stop_agent)
    heartbeat.start()
    try:
        return _report_run(
            config, context, sequencer, hub, read_secret, runner, prepare_repo, finished, write_token
        )
    finally:
        heartbeat.stop()


def _report_run(
    config: Config,
    context: dict[str, Any],
    sequencer: Sequencer,
    hub: HubClient,
    read_secret: Callable[[str, str], str],
    runner: ClaudeRunner,
    prepare_repo: Callable[[str, str, str], bool | None] | None,
    run_finished: threading.Event,
    write_token: Callable[[str], str] | None = None,
) -> int:
    """Work the run and report every step. Returns the exit code."""
    try:
        sequencer.send("started")
        if context.get("harness") not in SUPPORTED_HARNESSES:
            sequencer.send("failed", code="unsupported_harness", message="This agent type is not available yet.")
            return 1

        try:
            api_key = read_secret(config.project_id, config.anthropic_secret_name)
            _mark("secret_read")
        except Exception as error:  # noqa: BLE001 - any failure must fail the run; it never runs without a key
            log.error("secret unavailable: %s", type(error).__name__)
            sequencer.send("failed", code="secret_unavailable", message="The agent's credentials are unavailable.")
            return 1

        repository = context.get("repository")
        ref = context.get("ref") or "main"
        can_push = bool(context.get("can_write")) and bool(repository) and write_token is not None
        with tempfile.TemporaryDirectory(prefix="run-") as workdir:
            cwd = workdir
            if repository:
                if prepare_repo is None:
                    sequencer.send("failed", code="repo_unavailable", message="Repository access is not configured.")
                    return 1
                repo_dir = os.path.join(workdir, "repo")
                sequencer.send("progress", text=f"Preparing the repository {repository}")
                try:
                    has_files = prepare_repo(repository, ref, repo_dir)
                    _mark("repo_ready")
                except GitHubError as error:
                    # The detail is git's own message, already scrubbed of the token. Logged for operators and
                    # shortened for the channel, so people can see why a run stopped.
                    detail = " ".join((error.message or "").split())[:200]
                    log.error("repository preparation failed: %s %s", error.code, detail)
                    reason = f": {detail}" if detail else ""
                    sequencer.send(
                        "failed",
                        code=error.code,
                        message=f"The repository could not be prepared ({error.code}){reason}",
                    )
                    return 1
                if has_files is False:
                    # An empty repository: there is nothing to read. Say so to the agent, so it answers plainly.
                    os.makedirs(repo_dir, exist_ok=True)
                    can_push = False  # there is no clone to push from; the agent is told so below
                    prompt = (
                        f"The repository {repository} has no commits yet, so there are no files to read. "
                        "Tell the user that. Do not invent code or file contents.\n\n" + context["prompt"]
                    )
                else:
                    prompt = context["prompt"]
                    if can_push:
                        can_push = _enable_push(repo_dir, repository, write_token)
                cwd = repo_dir
            else:
                prompt = context["prompt"]
            command = build_claude_args(
                shlex.split(config.claude_binary),
                model=config.claude_model,
                prompt=INSTRUCTIONS + "\n\n" + (write_instructions(ref) + "\n\n" if can_push else "") + prompt,
                folder=cwd,
                write=can_push,
            )

            first_output = [True]

            def emit(text: str) -> None:
                if run_finished.is_set():
                    return
                if first_output[0]:
                    first_output[0] = False
                    _mark("first_output")
                try:
                    # The hub takes at most HUB_TEXT_LIMIT characters per event, and one assistant message can be longer.
                    for start in range(0, len(text), HUB_TEXT_LIMIT):
                        sequencer.send("output", text=text[start : start + HUB_TEXT_LIMIT])
                except HubError as error:
                    if error.code != "run_finished":
                        raise
                    # The run was cancelled or timed out on the hub. Stop the agent; nothing else to report.
                    run_finished.set()
                    runner.terminate()

            sequencer.send("progress", text="Running the agent")
            _mark("agent_started")
            # The key stays in this process: the proxy adds it to each request, and the CLI only sees the proxy.
            with ModelProxy(api_key) as proxy:
                outcome = runner.run(
                    command=command,
                    api_key=api_key,
                    base_url=proxy.url,
                    workdir=workdir,
                    cwd=cwd,
                    emit_text=emit,
                )
            _mark("agent_finished")
        if run_finished.is_set():
            return 0
        api_key = ""  # drop the reference as soon as the harness has finished

        if outcome.ok:
            sequencer.send("progress", text="Writing the reply")
            reply = extract_reply_files(outcome.final_text)
            for name, content in reply.files:
                sequencer.send("file", name=name, content=content)
            if reply.message:
                sequencer.send("message", text=clip(reply.message))
            sequencer.send("completed")
            _mark("completed")
            return 0
        sequencer.send("failed", code=outcome.error_code or "runtime_failed", message=outcome.error_message)
        return 1
    except HubError as error:
        log.error("hub refused or unreachable while reporting: %s %s", error.status_code, error.code)
        if error.status_code != 409:  # 409 means the run already finished on the hub; there is nothing to report
            try:
                sequencer.send("failed", code="hub_rejected", message=f"The hub refused the run's output (HTTP {error.status_code}).")
            except HubError:
                pass  # the hub is unreachable; its sweeper will fail the run
        return 4


def _enable_push(folder: str, repository: str, write_token: Callable[[str], str]) -> bool:
    """Give the clone a push credential for this run. Returns False, and the run goes on read-only, if that fails."""
    try:
        configure_push(folder, token=write_token(repository))
    except (PublishError, GitHubError) as error:
        log.error("push not enabled: %s", error.code)
        return False
    return True


def make_write_token(config: Config, read_secret: Callable[[str, str], str], http: httpx.Client) -> Callable[[str], str]:
    """A function that gives a write token for one repository. Called once per write run, before the agent starts."""

    def token(repository: str) -> str:
        if not config.github_app_id or not config.github_installation_id:
            raise GitHubError("github_not_configured")
        try:
            private_key = read_secret(config.project_id, config.github_key_secret_name)
        except Exception as error:
            raise GitHubError("github_key_unavailable", type(error).__name__) from error
        return installation_token(
            http,
            app_id=config.github_app_id,
            installation_id=config.github_installation_id,
            private_key_pem=private_key,
            repo_full_name=repository,
            write=True,
        )

    return token


def make_repo_preparer(
    config: Config, read_secret: Callable[[str, str], str], http: httpx.Client
) -> Callable[[str, str, str], bool]:
    """Build the function that prepares a repository: read the App key, get a token, clone one branch."""

    def prepare(repository: str, ref: str, destination: str) -> bool:
        if not config.github_app_id or not config.github_installation_id:
            raise GitHubError("github_not_configured")
        try:
            private_key = read_secret(config.project_id, config.github_key_secret_name)
        except Exception as error:
            raise GitHubError("github_key_unavailable", type(error).__name__) from error
        token = installation_token(
            http,
            app_id=config.github_app_id,
            installation_id=config.github_installation_id,
            private_key_pem=private_key,
            repo_full_name=repository,
        )
        private_key = ""
        return clone_repository(repo_full_name=repository, ref=ref, token=token, destination=destination)

    return prepare
