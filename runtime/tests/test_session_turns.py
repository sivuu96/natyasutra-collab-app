"""Session turns on a warm instance: resume the CLI's conversation, fall back to the transcript, and clean up.

The CLI is replaced by a script that keeps its conversation in HOME, as the real CLI does. Resuming a session
therefore needs the same HOME and folder as before, which is what the workspace registry must keep.
"""

import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from collab_runtime.harness import ClaudeRunner
from collab_runtime.main import Config, run
from collab_runtime.workspaces import Workspaces

API_KEY = "sk-ant-api03-TESTKEYVALUE12345"

FAKE_SESSION_CLI = """
import json, os, sys
argv = sys.argv[1:]
prompt = argv[argv.index("-p") + 1]
if "--resume" in argv:
    sid = argv[argv.index("--resume") + 1]
    mode = "resume"
elif "--session-id" in argv:
    sid = argv[argv.index("--session-id") + 1]
    mode = "new"
else:
    sid = None
    mode = "plain"
store = os.path.join(os.environ["HOME"], ".fake-cli")
os.makedirs(store, exist_ok=True)
path = os.path.join(store, str(sid) + ".json")
if mode == "plain":
    turns = [prompt]
elif mode == "resume":
    if not os.path.exists(path):
        print(json.dumps({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "no conversation"}), flush=True)
        sys.exit(0)
    turns = json.load(open(path))
else:
    turns = []
if mode != "plain":
    turns.append(prompt)
    json.dump(turns, open(path, "w"))
if "FAIL" in prompt:
    print(json.dumps({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "boom"}), flush=True)
    sys.exit(1)
answer = "mode=" + mode + " turns=" + str(len(turns)) + " prompt=" + prompt
print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": answer}]}}), flush=True)
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": answer, "session_id": sid}), flush=True)
"""


class SessionHub:
    """Serves one turn's context at a time, and records what the runtime reported."""

    def __init__(self) -> None:
        self.context: dict[str, Any] = {}
        self.events: list[dict[str, Any]] = []

    def exchange(self, *, lease: str, execution_name: str) -> dict[str, Any]:
        return dict(self.context)

    def post_event(self, *, runtime_token: str, event: dict[str, Any]) -> None:
        self.events.append(event)


def _binary(tmp_path: Path) -> str:
    script = tmp_path / "fake_session_cli.py"
    script.write_text(FAKE_SESSION_CLI, encoding="utf-8")
    return f'"{Path(sys.executable).as_posix()}" "{script.as_posix()}"'


def _config(binary: str) -> Config:
    return Config(
        hub_base_url="https://hub.example",
        project_id="natyasutra-agents",
        anthropic_secret_name="anthropic-api-key",
        claude_model="claude-haiku-5-5",
        claude_binary=binary,
        timeout_seconds=20,
    )


class Prepares:
    """Stands in for the repository step: records calls and creates a clone marker."""

    def __init__(self) -> None:
        self.calls: list[bool] = []

    def __call__(self, repository: str, ref: str, destination: str, refresh: bool = False) -> bool:
        self.calls.append(refresh)
        os.makedirs(os.path.join(destination, ".git"), exist_ok=True)
        return True


def _turn(
    tmp_path: Path,
    hub: SessionHub,
    workspaces: Workspaces | None,
    *,
    session: str,
    invocation: str,
    previous: str | None,
    message: str,
    transcript: str,
    prepares: Prepares,
    repository: str | None = "owner/repo",
) -> tuple[int, str | None]:
    hub.context = {
        "invocation_id": invocation,
        "prompt": transcript,
        "agent_handle": "claude",
        "harness": "claude_code",
        "runtime_token": "runtime-token",
        "runtime_expires_in": 900,
        "repository": repository,
        "ref": "main",
        "session_id": session,
        "message": message,
        "previous_invocation_id": previous,
    }
    hub.events = []
    code = run(
        _config(_binary(tmp_path)),
        env={},
        hub=hub,  # type: ignore[arg-type]
        read_secret=lambda _project, _name: API_KEY,
        runner=ClaudeRunner(timeout_seconds=20),
        prepare_repo=prepares,
        lease="lease-value",
        execution_name="service/inv/1",
        workspaces=workspaces,
    )
    answer = next((e["text"] for e in hub.events if e["type"] == "message"), None)
    return code, answer


def test_a_first_turn_starts_a_conversation_under_a_chosen_id(tmp_path: Path) -> None:
    session = str(uuid.uuid4())
    workspaces = Workspaces(str(tmp_path / "ws"))
    hub, prepares = SessionHub(), Prepares()
    code, answer = _turn(
        tmp_path, hub, workspaces, session=session, invocation=str(uuid.uuid4()), previous=None,
        message="hello", transcript="TRANSCRIPT hello", prepares=prepares,
    )
    assert code == 0
    assert answer == "mode=new turns=1 prompt=TRANSCRIPT hello"
    assert prepares.calls == [False]
    assert workspaces.sessions() == [session]


def test_a_follow_up_resumes_the_same_conversation_with_only_the_new_message(tmp_path: Path) -> None:
    session = str(uuid.uuid4())
    workspaces = Workspaces(str(tmp_path / "ws"))
    hub, prepares = SessionHub(), Prepares()
    first = str(uuid.uuid4())
    _turn(tmp_path, hub, workspaces, session=session, invocation=first, previous=None,
          message="one", transcript="TRANSCRIPT one", prepares=prepares)
    second = str(uuid.uuid4())
    code, answer = _turn(
        tmp_path, hub, workspaces, session=session, invocation=second, previous=first,
        message="two", transcript="TRANSCRIPT one ... two", prepares=prepares,
    )
    assert code == 0
    # The CLI already holds turn one, so it answers from its own conversation. Turn two's text arrives alone.
    assert answer == "mode=resume turns=2 prompt=two"
    assert prepares.calls == [False, True]  # the clone is refreshed, not cloned again


def test_a_follow_up_that_this_instance_missed_starts_fresh_from_the_transcript(tmp_path: Path) -> None:
    session = str(uuid.uuid4())
    workspaces = Workspaces(str(tmp_path / "ws"))
    hub, prepares = SessionHub(), Prepares()
    _turn(tmp_path, hub, workspaces, session=session, invocation=str(uuid.uuid4()), previous=None,
          message="one", transcript="TRANSCRIPT one", prepares=prepares)
    # The hub says the previous turn ran elsewhere, so this instance's copy is stale.
    code, answer = _turn(
        tmp_path, hub, workspaces, session=session, invocation=str(uuid.uuid4()), previous=str(uuid.uuid4()),
        message="two", transcript="FULL TRANSCRIPT", prepares=prepares,
    )
    assert code == 0
    assert answer == "mode=new turns=1 prompt=FULL TRANSCRIPT"


def test_a_failed_turn_discards_the_workspace_so_the_next_turn_starts_from_the_transcript(tmp_path: Path) -> None:
    session = str(uuid.uuid4())
    workspaces = Workspaces(str(tmp_path / "ws"))
    hub, prepares = SessionHub(), Prepares()
    first = str(uuid.uuid4())
    _turn(tmp_path, hub, workspaces, session=session, invocation=first, previous=None,
          message="one", transcript="TRANSCRIPT one", prepares=prepares)
    failed = str(uuid.uuid4())
    code, _ = _turn(tmp_path, hub, workspaces, session=session, invocation=failed, previous=first,
                    message="FAIL here", transcript="TRANSCRIPT one ... FAIL", prepares=prepares)
    assert code == 1
    assert workspaces.sessions() == []  # the half-written conversation is gone
    code, answer = _turn(tmp_path, hub, workspaces, session=session, invocation=str(uuid.uuid4()), previous=failed,
                         message="three", transcript="TRANSCRIPT three", prepares=prepares)
    assert code == 0
    assert answer == "mode=new turns=1 prompt=TRANSCRIPT three"


def test_a_successful_turn_is_recorded_so_the_next_one_can_resume(tmp_path: Path) -> None:
    session = str(uuid.uuid4())
    workspaces = Workspaces(str(tmp_path / "ws"))
    hub, prepares = SessionHub(), Prepares()
    first = str(uuid.uuid4())
    _turn(tmp_path, hub, workspaces, session=session, invocation=first, previous=None,
          message="one", transcript="T1", prepares=prepares)
    item = workspaces.get(session)
    assert item is not None and item.last_invocation_id == first
    assert os.path.isdir(item.home) and os.path.isdir(item.repo)


def test_a_mention_never_uses_or_creates_a_workspace(tmp_path: Path) -> None:
    workspaces = Workspaces(str(tmp_path / "ws"))
    hub, prepares = SessionHub(), Prepares()
    code, answer = _turn(
        tmp_path, hub, workspaces, session=None, invocation=str(uuid.uuid4()), previous=None,
        message="", transcript="just a mention", prepares=prepares,
    )
    assert code == 0
    assert answer == "mode=plain turns=1 prompt=just a mention"  # no session flags on a mention
    assert workspaces.sessions() == []


def test_without_a_workspace_registry_a_session_turn_runs_fresh_each_time(tmp_path: Path) -> None:
    session = str(uuid.uuid4())
    hub, prepares = SessionHub(), Prepares()
    first = str(uuid.uuid4())
    _turn(tmp_path, hub, None, session=session, invocation=first, previous=None,
          message="one", transcript="T1", prepares=prepares)
    code, answer = _turn(tmp_path, hub, None, session=session, invocation=str(uuid.uuid4()), previous=first,
                         message="two", transcript="T1 two", prepares=prepares)
    assert code == 0
    assert answer == "mode=plain turns=1 prompt=T1 two"  # the job path never resumes


def test_a_session_id_that_is_not_a_uuid_is_refused_without_running(tmp_path: Path) -> None:
    workspaces = Workspaces(str(tmp_path / "ws"))
    hub, prepares = SessionHub(), Prepares()
    code, _ = _turn(tmp_path, hub, workspaces, session="../escape", invocation=str(uuid.uuid4()), previous=None,
                    message="x", transcript="x", prepares=prepares)
    assert code == 1
    failures = [e for e in hub.events if e["type"] == "failed"]
    assert failures and failures[0]["code"] == "bad_session"
    assert not (tmp_path / "escape").exists()


def test_the_conversation_file_is_only_in_the_session_home(tmp_path: Path) -> None:
    session = str(uuid.uuid4())
    workspaces = Workspaces(str(tmp_path / "ws"))
    hub, prepares = SessionHub(), Prepares()
    _turn(tmp_path, hub, workspaces, session=session, invocation=str(uuid.uuid4()), previous=None,
          message="one", transcript="T1", prepares=prepares)
    saved = list((Path(workspaces.get(session).home) / ".fake-cli").glob("*.json"))  # type: ignore[union-attr]
    assert len(saved) == 1
    assert json.loads(saved[0].read_text(encoding="utf-8")) == ["T1"]
