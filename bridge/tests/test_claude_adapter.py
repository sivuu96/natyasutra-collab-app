"""Claude Code adapter: argument construction, stream parsing and process handling.

The line samples below are copied from a live run of Claude Code 2.1.x (see the adapter docstring).
The process test uses a stand-in script that prints the same shapes, so it runs without a model.
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

from collab_bridge.adapters.base import RunRequest
from collab_bridge.adapters.claude_code import ClaudeCodeAdapter, _parse_line, _scrub

LIVE_SAMPLES = {
    "init": {"type": "system", "subtype": "init", "session_id": "caf2f2ca-6e95-4c8a-84c5-9f9ed87a06aa"},
    "text_delta": {
        "type": "stream_event",
        "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "The"}},
    },
    "assistant_text": {"type": "assistant", "message": {"content": [{"type": "text", "text": "Hi there."}]}},
    "assistant_tool_use": {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "name": "Write", "input": {}}]},
    },
    "denied_result": {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "session_id": "abc",
        "result": "Blocked.",
        "permission_denials": [{"tool_name": "Write", "tool_use_id": "t1", "tool_input": {}}],
    },
    "ok_result": {"type": "result", "subtype": "success", "is_error": False, "session_id": "abc", "result": "Done"},
    "error_result": {"type": "result", "subtype": "error_max_turns", "is_error": True, "result": "too many turns"},
}


@pytest.mark.parametrize(
    "name,expected_kind",
    [
        ("init", "session"),
        ("text_delta", "text_delta"),
        ("assistant_text", "text_final"),
    ],
)
def test_parse_live_shapes(name: str, expected_kind: str) -> None:
    events = _parse_line(json.dumps(LIVE_SAMPLES[name]).encode())
    assert [e.kind for e in events] == [expected_kind]


def test_tool_use_blocks_are_not_text() -> None:
    assert _parse_line(json.dumps(LIVE_SAMPLES["assistant_tool_use"]).encode()) == []


def test_denied_result_yields_permission_then_completed() -> None:
    events = _parse_line(json.dumps(LIVE_SAMPLES["denied_result"]).encode())
    assert [e.kind for e in events] == ["permission_denied", "completed"]
    assert events[0].tools == ["Write"]
    assert events[1].session_ref == "abc"


def test_error_result_is_a_failure_with_scrubbed_message() -> None:
    events = _parse_line(json.dumps(LIVE_SAMPLES["error_result"]).encode())
    assert events[0].kind == "failed"
    assert events[0].code == "harness_error_max_turns"


def test_unknown_and_malformed_lines_are_ignored() -> None:
    assert _parse_line(b"not json") == []
    assert _parse_line(b'{"type": "future_event_type", "x": 1}') == []
    assert _parse_line(b"[1,2,3]") == []


def test_stderr_secrets_are_redacted() -> None:
    assert "hunter2" not in _scrub("password=hunter2 failed")
    assert "[redacted]" in _scrub("api_key: abc123")


def test_arguments_put_allowed_tools_before_prompt_and_resume_when_set(tmp_path: Path) -> None:
    adapter = ClaudeCodeAdapter()
    request = RunRequest(
        invocation_id="i1",
        prompt="hello",
        cwd=tmp_path,
        allowed_tools=["Glob", "Grep", "Read"],
        resume_session="s-1",
        harness_bin="claude",
    )
    args = adapter.build_args(request)
    assert args[0] == "claude"
    assert args[1:4] == ["--allowedTools", "Glob", "Grep"]
    assert "--resume" in args and args.index("--resume") < args.index("-p")
    assert args[args.index("-p") + 1] == "hello"
    assert "--output-format" in args and "stream-json" in args
    assert "--include-partial-messages" in args


def test_discover_reports_missing_binary(tmp_path: Path) -> None:
    status = ClaudeCodeAdapter().discover(str(tmp_path / "no-such-claude"))
    assert status.available is False


async def _collect(adapter: ClaudeCodeAdapter, request: RunRequest) -> list:
    return [event async for event in adapter.run(request)]


def test_run_streams_events_from_a_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Drive the real subprocess pipeline with a stand-in script that prints live-shaped lines."""
    script = tmp_path / "fake_claude.py"
    lines = [
        LIVE_SAMPLES["init"],
        LIVE_SAMPLES["text_delta"],
        LIVE_SAMPLES["assistant_text"],
        LIVE_SAMPLES["denied_result"],
    ]
    # repr() keeps Python literals (False, not false) so the stand-in script is valid Python.
    script.write_text(
        f"import json, sys\nfor line in {lines!r}:\n    print(json.dumps(line), flush=True)\n",
        encoding="utf-8",
    )
    adapter = ClaudeCodeAdapter()
    monkeypatch.setattr(adapter, "build_args", lambda request: [sys.executable, str(script)])
    request = RunRequest(invocation_id="inv-1", prompt="x", cwd=tmp_path, allowed_tools=["Read"])
    events = asyncio.run(_collect(adapter, request))
    assert [e.kind for e in events] == ["session", "text_delta", "text_final", "permission_denied", "completed"]


def test_run_without_result_reports_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = tmp_path / "exits.py"
    script.write_text("import sys\nsys.stderr.write('boom token=secret123')\nsys.exit(3)\n", encoding="utf-8")
    adapter = ClaudeCodeAdapter()
    monkeypatch.setattr(adapter, "build_args", lambda request: [sys.executable, str(script)])
    request = RunRequest(invocation_id="inv-2", prompt="x", cwd=tmp_path, allowed_tools=["Read"])
    events = asyncio.run(_collect(adapter, request))
    assert events[-1].kind == "failed"
    assert events[-1].code == "harness_exit"
    assert "secret123" not in (events[-1].message or "")


def test_missing_executable_is_a_clean_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = ClaudeCodeAdapter()
    monkeypatch.setattr(adapter, "build_args", lambda request: [str(tmp_path / "does-not-exist.exe")])
    request = RunRequest(invocation_id="inv-3", prompt="x", cwd=tmp_path, allowed_tools=["Read"])
    events = asyncio.run(_collect(adapter, request))
    assert events[0].kind == "failed"
    assert events[0].code == "harness_unavailable"
