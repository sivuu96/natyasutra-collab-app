"""Runtime entrypoint. One job execution runs one invocation, then exits.

Flow: exchange the run lease -> read the API key from Secret Manager -> run the harness in an empty workspace
-> report events to the hub -> exit. The lease is removed from the environment immediately after it is read.
"""

import logging
import os
import shlex
import signal
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from collab_runtime.github import GitHubError, clone_repository, installation_token
from collab_runtime.harness import ClaudeRunner, build_claude_args
from collab_runtime.hub import HubClient, HubError

log = logging.getLogger("collab.runtime")

SUPPORTED_HARNESSES = {"claude_code"}


@dataclass(frozen=True)
class Config:
    hub_base_url: str
    project_id: str
    anthropic_secret_name: str
    claude_model: str
    claude_binary: str
    timeout_seconds: float
    github_key_secret_name: str = "github-app-key"
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
            github_app_id=env.get("GITHUB_APP_ID") or None,
            github_installation_id=env.get("GITHUB_INSTALLATION_ID") or None,
        )


class Sequencer:
    """Monotonic event numbers. The hub rejects nothing for a repeat, so a retried event is harmless."""

    def __init__(self, hub: HubClient, runtime_token: str) -> None:
        self._hub = hub
        self._token = runtime_token
        self._next = 0

    def send(self, event_type: str, **fields: object) -> None:
        event = {"sequence": self._next, "type": event_type, **{k: v for k, v in fields.items() if v is not None}}
        self._hub.post_event(runtime_token=self._token, event=event)
        self._next += 1


def run(
    config: Config,
    *,
    env: dict[str, str],
    hub: HubClient,
    read_secret: Callable[[str, str], str],
    runner: ClaudeRunner,
    prepare_repo: Callable[[str, str, str], None] | None = None,
) -> int:
    """Run one invocation. Returns the process exit code. Every outcome is reported to the hub first."""
    lease = env.pop("RUN_LEASE", "")  # the lease must not remain in the environment
    execution_name = env.get("CLOUD_RUN_EXECUTION", "")
    if not lease or not execution_name:
        log.error("missing run lease or execution name")
        return 2

    try:
        context = hub.exchange(lease=lease, execution_name=execution_name)
    except HubError as error:
        log.error("lease exchange refused: %s", error.code)
        return 3  # no runtime token exists, so the run cannot be reported; the hub's run timeout handles it

    sequencer = Sequencer(hub, context["runtime_token"])
    try:
        sequencer.send("started")
        if context.get("harness") not in SUPPORTED_HARNESSES:
            sequencer.send("failed", code="unsupported_harness", message="This agent type is not available yet.")
            return 1

        try:
            api_key = read_secret(config.project_id, config.anthropic_secret_name)
        except Exception as error:  # a missing secret must fail the run, never run without a key
            log.error("secret unavailable: %s", type(error).__name__)
            sequencer.send("failed", code="secret_unavailable", message="The agent's credentials are unavailable.")
            return 1

        repository = context.get("repository")
        ref = context.get("ref") or "main"
        with tempfile.TemporaryDirectory(prefix="run-") as workdir:
            cwd = workdir
            if repository:
                if prepare_repo is None:
                    sequencer.send("failed", code="repo_unavailable", message="Repository access is not configured.")
                    return 1
                repo_dir = os.path.join(workdir, "repo")
                try:
                    prepare_repo(repository, ref, repo_dir)
                except GitHubError as error:
                    log.error("repository preparation failed: %s", error.code)
                    sequencer.send("failed", code=error.code, message="The repository could not be prepared.")
                    return 1
                cwd = repo_dir
            command = build_claude_args(
                shlex.split(config.claude_binary), model=config.claude_model, prompt=context["prompt"]
            )
            outcome = runner.run(
                command=command,
                api_key=api_key,
                workdir=workdir,
                cwd=cwd,
                emit_text=lambda text: sequencer.send("output", text=text),
            )
        api_key = ""  # drop the reference as soon as the harness has finished

        if outcome.ok:
            if outcome.final_text:
                sequencer.send("message", text=outcome.final_text)
            sequencer.send("completed", harness_session_ref=outcome.session_id)
            return 0
        sequencer.send("failed", code=outcome.error_code or "runtime_failed", message=outcome.error_message)
        return 1
    except HubError as error:
        log.error("hub unreachable while reporting: %s", error.code)
        return 4


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s", stream=sys.stderr)
    env = dict(os.environ)
    os.environ.pop("RUN_LEASE", None)  # the lease must not remain in the process environment
    try:
        config = Config.from_env(env)
    except KeyError as error:
        log.error("missing configuration: %s", error.args[0])
        return 2

    from collab_runtime.secrets import read_secret

    runner = ClaudeRunner(timeout_seconds=config.timeout_seconds)
    signal.signal(signal.SIGTERM, lambda _signum, _frame: runner.terminate())
    return run(
        config,
        env=env,
        hub=HubClient(config.hub_base_url),
        read_secret=read_secret,
        runner=runner,
        prepare_repo=make_repo_preparer(config, read_secret, httpx.Client(timeout=httpx.Timeout(20.0))),
    )


def make_repo_preparer(
    config: Config, read_secret: Callable[[str, str], str], http: httpx.Client
) -> Callable[[str, str, str], None]:
    """Build the function that prepares a repository: read the App key, get a token, clone one branch."""

    def prepare(repository: str, ref: str, destination: str) -> None:
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
        clone_repository(repo_full_name=repository, ref=ref, token=token, destination=destination)

    return prepare
