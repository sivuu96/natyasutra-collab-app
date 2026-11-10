# ruff: noqa: E501  (embedded fake-harness scripts are long string literals)
"""Runtime tests: harness parsing, redaction, the subprocess contract, and the full run flow.

The Claude CLI is replaced by a small Python script that speaks the same stream-json format. No test calls
Anthropic, the hub, or Google Cloud.
"""

import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from collab_runtime import main as runtime_main
from collab_runtime.harness import (
    ClaudeRunner,
    build_claude_args,
    parse_stream_line,
    redact,
)
from collab_runtime.hub import HubClient, HubError
from collab_runtime.main import Config, run

API_KEY = "sk-ant-api03-TESTKEYVALUE12345"


class FakeHub:
    def __init__(self, *, refuse: str | None = None, context: dict[str, Any] | None = None) -> None:
        self.refuse = refuse
        self.context = context or {
            "invocation_id": "11111111-1111-1111-1111-111111111111",
            "prompt": "summarise the open decisions",
            "agent_handle": "claude",
            "harness": "claude_code",
            "runtime_token": "runtime-token",
            "runtime_expires_in": 900,
        }
        self.events: list[dict[str, Any]] = []
        self.exchanged: list[tuple[str, str]] = []

    def exchange(self, *, lease: str, execution_name: str) -> dict[str, Any]:
        self.exchanged.append((lease, execution_name))
        if self.refuse:
            raise HubError(401, self.refuse)
        return dict(self.context)

    def post_event(self, *, runtime_token: str, event: dict[str, Any]) -> None:
        assert runtime_token == "runtime-token"
        self.events.append(event)


def _write_fake_harness(tmp_path: Path, body: str) -> str:
    script = tmp_path / "fake_claude.py"
    script.write_text(body, encoding="utf-8")
    # Quoted, forward-slash paths so shlex.split works on Windows and Linux alike.
    return f'"{Path(sys.executable).as_posix()}" "{script.as_posix()}"'


SUCCESS_HARNESS = """
import json, os
key = os.environ.get("ANTHROPIC_API_KEY", "")
print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Checking. key=" + key}]}}), flush=True)
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "Done. " + key, "session_id": "sess-1"}), flush=True)
"""

ERROR_HARNESS = """
import json
print(json.dumps({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "boom"}), flush=True)
"""

SILENT_HARNESS = """
import sys
sys.stderr.write("no json here")
"""

SLOW_HARNESS = """
import time
time.sleep(30)
"""


def _config(binary: str, timeout: float = 20) -> Config:
    return Config(
        hub_base_url="https://hub.example",
        project_id="natyasutra-agents",
        anthropic_secret_name="anthropic-api-key",
        claude_model="claude-haiku-5-5",
        claude_binary=binary,
        timeout_seconds=timeout,
    )


def _env(lease: str = "lease-secret-value") -> dict[str, str]:
    return {"RUN_LEASE": lease, "CLOUD_RUN_EXECUTION": "projects/p/locations/us-west1/jobs/j/executions/e1"}


# ---- pure functions ---------------------------------------------------------------------------------


def test_redaction_removes_the_key_value_and_anything_shaped_like_a_key() -> None:
    text = f"value {API_KEY} and another sk-ant-zzzzzzzzzz"
    result = redact(text, API_KEY)
    assert API_KEY not in result
    assert "sk-ant-zzzzzzzzzz" not in result
    assert result.count("[redacted]") == 2


def test_stream_lines_are_parsed_and_junk_is_ignored() -> None:
    assistant = '{"type":"assistant","message":{"content":[{"type":"text","text":"hi"},{"type":"tool_use"}]}}'
    result = '{"type":"result","subtype":"success","is_error":false,"result":"ok","session_id":"s"}'
    assert parse_stream_line(assistant).text == "hi"
    parsed = parse_stream_line(result)
    assert parsed.kind == "result" and not parsed.is_error and parsed.session_id == "s"
    assert parse_stream_line("not json").kind == "other"
    assert parse_stream_line("[1, 2]").kind == "other"


def test_claude_is_run_read_only_with_the_prompt_last_before_format_flags() -> None:
    args = build_claude_args(["claude"], model="claude-haiku-5-5", prompt="hello")
    assert args[:4] == ["claude", "--allowedTools", "Read", "Glob"]
    assert "Bash" not in args and "Edit" not in args and "Write" not in args
    assert args[args.index("--model") + 1] == "claude-haiku-5-5"
    assert args[args.index("-p") + 1] == "hello"


# ---- the subprocess contract ------------------------------------------------------------------------


def test_harness_output_is_streamed_redacted_and_the_key_reaches_only_the_child(tmp_path: Path) -> None:
    emitted: list[str] = []
    outcome = ClaudeRunner(timeout_seconds=20).run(
        command=_split(_write_fake_harness(tmp_path, SUCCESS_HARNESS)),
        api_key=API_KEY,
        workdir=str(tmp_path),
        emit_text=emitted.append,
    )
    assert outcome.ok
    assert outcome.session_id == "sess-1"
    assert all(API_KEY not in text for text in emitted)
    assert API_KEY not in outcome.final_text
    assert any("[redacted]" in text for text in emitted)


def test_a_harness_error_result_is_reported_as_a_failure(tmp_path: Path) -> None:
    outcome = ClaudeRunner(timeout_seconds=20).run(
        command=_split(_write_fake_harness(tmp_path, ERROR_HARNESS)),
        api_key=API_KEY,
        workdir=str(tmp_path),
        emit_text=lambda _t: None,
    )
    assert not outcome.ok
    assert outcome.error_code == "harness_error"


def test_a_harness_without_a_result_fails_with_its_stderr_tail(tmp_path: Path) -> None:
    outcome = ClaudeRunner(timeout_seconds=20).run(
        command=_split(_write_fake_harness(tmp_path, SILENT_HARNESS)),
        api_key=API_KEY,
        workdir=str(tmp_path),
        emit_text=lambda _t: None,
    )
    assert outcome.error_code == "no_result"
    assert "no json here" in (outcome.error_message or "")


def test_a_slow_harness_is_stopped_at_the_time_limit(tmp_path: Path) -> None:
    outcome = ClaudeRunner(timeout_seconds=1).run(
        command=_split(_write_fake_harness(tmp_path, SLOW_HARNESS)),
        api_key=API_KEY,
        workdir=str(tmp_path),
        emit_text=lambda _t: None,
    )
    assert outcome.error_code == "timeout"


def test_a_missing_binary_is_reported_not_raised(tmp_path: Path) -> None:
    outcome = ClaudeRunner(timeout_seconds=5).run(
        command=["/definitely/not/here/claude"],
        api_key=API_KEY,
        workdir=str(tmp_path),
        emit_text=lambda _t: None,
    )
    assert outcome.error_code == "harness_missing"


def _split(command: str) -> list[str]:
    import shlex

    return shlex.split(command)


# ---- the full run flow ------------------------------------------------------------------------------


def test_a_successful_run_reports_started_output_message_and_completed(tmp_path: Path) -> None:
    hub = FakeHub()
    env = _env()
    code = run(
        _config(_write_fake_harness(tmp_path, SUCCESS_HARNESS)),
        env=env,
        hub=hub,  # type: ignore[arg-type]
        read_secret=lambda _p, _n: API_KEY,
        runner=ClaudeRunner(timeout_seconds=20),
    )

    assert code == 0
    types = [event["type"] for event in hub.events]
    assert types[0] == "started"
    assert types[-2:] == ["message", "completed"]
    assert [event["sequence"] for event in hub.events] == list(range(len(hub.events)))
    assert all(API_KEY not in str(event) for event in hub.events)


def test_the_lease_is_removed_from_the_environment_before_work_starts(tmp_path: Path) -> None:
    env = _env("lease-value-to-remove")
    run(
        _config(_write_fake_harness(tmp_path, SUCCESS_HARNESS)),
        env=env,
        hub=FakeHub(),  # type: ignore[arg-type]
        read_secret=lambda _p, _n: API_KEY,
        runner=ClaudeRunner(timeout_seconds=20),
    )
    assert "RUN_LEASE" not in env


def test_a_refused_lease_stops_before_any_work(tmp_path: Path) -> None:
    hub = FakeHub(refuse="lease_expired")
    called: list[str] = []
    code = run(
        _config(_write_fake_harness(tmp_path, SUCCESS_HARNESS)),
        env=_env(),
        hub=hub,  # type: ignore[arg-type]
        read_secret=lambda _p, _n: called.append("read") or API_KEY,
        runner=ClaudeRunner(timeout_seconds=20),
    )
    assert code == 3
    assert hub.events == []
    assert called == []  # no secret is read for a refused run


def test_a_missing_secret_fails_the_run_instead_of_running_without_a_key(tmp_path: Path) -> None:
    hub = FakeHub()

    def no_secret(_project: str, _name: str) -> str:
        raise RuntimeError("secret not found")

    code = run(
        _config(_write_fake_harness(tmp_path, SUCCESS_HARNESS)),
        env=_env(),
        hub=hub,  # type: ignore[arg-type]
        read_secret=no_secret,
        runner=ClaudeRunner(timeout_seconds=20),
    )
    assert code == 1
    assert hub.events[-1] == {
        "sequence": 1,
        "type": "failed",
        "code": "secret_unavailable",
        "message": hub.events[-1]["message"],
    }


def test_an_unsupported_harness_is_reported(tmp_path: Path) -> None:
    hub = FakeHub(context={"prompt": "x", "harness": "codex", "runtime_token": "runtime-token"})
    code = run(
        _config(_write_fake_harness(tmp_path, SUCCESS_HARNESS)),
        env=_env(),
        hub=hub,  # type: ignore[arg-type]
        read_secret=lambda _p, _n: API_KEY,
        runner=ClaudeRunner(timeout_seconds=20),
    )
    assert code == 1
    assert hub.events[-1]["code"] == "unsupported_harness"


def test_missing_lease_is_a_configuration_error(tmp_path: Path) -> None:
    code = run(
        _config("claude"),
        env={"CLOUD_RUN_EXECUTION": "x"},
        hub=FakeHub(),  # type: ignore[arg-type]
        read_secret=lambda _p, _n: API_KEY,
        runner=ClaudeRunner(timeout_seconds=20),
    )
    assert code == 2


def test_hub_client_sends_the_lease_as_a_bearer_token_and_retries_server_errors() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"runtime_token": "t"})

    client = HubClient("https://hub.example", http=httpx.Client(transport=httpx.MockTransport(handler)), backoff=0)
    client.post_event(runtime_token="t", event={"sequence": 0, "type": "started"})
    assert len(calls) == 2
    assert calls[-1].headers["authorization"] == "Bearer t"


def test_hub_client_stops_retrying_on_a_refusal() -> None:
    client = HubClient(
        "https://hub.example",
        http=httpx.Client(
            transport=httpx.MockTransport(lambda _r: httpx.Response(409, json={"detail": "run_finished"}))
        ),
        backoff=0,
    )
    with pytest.raises(HubError) as error:
        client.post_event(runtime_token="t", event={"sequence": 0, "type": "output"})
    assert error.value.code == "run_finished"


def test_main_reports_a_missing_configuration_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HUB_BASE_URL", raising=False)
    assert runtime_main.main() == 2


SLOW_SUCCESS_HARNESS = """
import json, time
time.sleep(0.7)
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "ok", "session_id": "s"}), flush=True)
"""

NOISY_HARNESS = """
import json, time
print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "first"}]}}), flush=True)
time.sleep(20)
print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "never sent"}]}}), flush=True)
"""


class RunFinishedAfterFirstOutput(FakeHub):
    """Behaves like a hub that cancelled the run after its first output: every later event is refused."""

    def post_event(self, *, runtime_token: str, event: dict[str, Any]) -> None:
        if any(e["type"] == "output" for e in self.events):
            raise HubError(409, "run_finished")  # every event after the cancellation is refused
        super().post_event(runtime_token=runtime_token, event=event)


def test_heartbeats_and_progress_are_sent_while_the_agent_works(tmp_path: Path) -> None:
    hub = FakeHub()
    config = _config(_write_fake_harness(tmp_path, SLOW_SUCCESS_HARNESS))
    config = Config(**{**config.__dict__, "heartbeat_seconds": 0.05})
    code = run(
        config,
        env=_env(),
        hub=hub,  # type: ignore[arg-type]
        read_secret=lambda _p, _n: API_KEY,
        runner=ClaudeRunner(timeout_seconds=20),
    )
    assert code == 0
    types = [event["type"] for event in hub.events]
    assert "heartbeat" in types
    assert any(e["type"] == "progress" and e["text"] == "Running the agent" for e in hub.events)
    assert [e["sequence"] for e in hub.events] == sorted(e["sequence"] for e in hub.events)
    assert len({e["sequence"] for e in hub.events}) == len(hub.events)


def test_a_run_cancelled_on_the_hub_stops_the_agent_promptly(tmp_path: Path) -> None:
    import time

    hub = RunFinishedAfterFirstOutput()
    started = time.monotonic()
    config = _config(_write_fake_harness(tmp_path, NOISY_HARNESS))
    config = Config(**{**config.__dict__, "heartbeat_seconds": 0.2})
    code = run(
        config,
        env=_env(),
        hub=hub,  # type: ignore[arg-type]
        read_secret=lambda _p, _n: API_KEY,
        runner=ClaudeRunner(timeout_seconds=60),
    )
    assert code == 0
    assert time.monotonic() - started < 15  # the 20-second sleep was cut short
    assert all("never sent" not in str(e) for e in hub.events)
