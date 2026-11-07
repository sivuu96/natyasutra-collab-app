"""Codex adapter against a stand-in app-server that speaks the verified JSON-RPC subset."""

import asyncio
import contextlib
import sys
from pathlib import Path

import pytest

from collab_bridge.adapters import codex as codex_module
from collab_bridge.adapters.base import RunRequest
from collab_bridge.adapters.codex import CodexAdapter, launch_args

STANDIN = Path(__file__).with_name("fake_codex_app_server.py")


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch) -> CodexAdapter:
    monkeypatch.setattr(codex_module, "launch_args", lambda binary: [sys.executable, str(STANDIN)])
    return CodexAdapter()


def _request(tmp_path: Path, prompt: str, invocation: str = "inv-1", resume: str | None = None) -> RunRequest:
    return RunRequest(
        invocation_id=invocation,
        prompt=prompt,
        cwd=tmp_path,
        allowed_tools=["Read"],
        resume_session=resume,
        harness_bin="codex-standin",
    )


def test_windows_cmd_shims_are_routed_through_cmd(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    assert launch_args(r"C:\npm\codex.cmd") == ["cmd", "/c", r"C:\npm\codex.cmd", "app-server"]
    assert launch_args(r"C:\bin\codex.exe") == [r"C:\bin\codex.exe", "app-server"]


async def _collect(agen) -> list:  # type: ignore[no-untyped-def]
    async with contextlib.aclosing(agen) as stream:
        return [event async for event in stream]


def test_plain_turn_streams_and_completes(adapter: CodexAdapter, tmp_path: Path) -> None:
    events = asyncio.run(_collect(adapter.run(_request(tmp_path, "Reply with CODEX OK"))))
    kinds = [e.kind for e in events]
    assert kinds[0] == "session"
    assert events[0].session_ref == "thread-test-1"
    assert "text_delta" in kinds
    assert [e.text for e in events if e.kind == "text_final"] == ["CODEX OK"]
    assert kinds[-1] == "completed"


def test_resume_uses_the_stored_thread(adapter: CodexAdapter, tmp_path: Path) -> None:
    events = asyncio.run(_collect(adapter.run(_request(tmp_path, "hello", resume="thread-test-1"))))
    assert events[0].kind == "session" and events[0].session_ref == "thread-test-1"
    assert events[-1].kind == "completed"


def test_file_change_approval_is_answered_on_the_same_connection(adapter: CodexAdapter, tmp_path: Path) -> None:
    async def scenario() -> list:
        seen = []
        agen = adapter.run(_request(tmp_path, "WRITE notes"))
        async for event in agen:
            seen.append(event)
            if event.kind == "approval_request":
                assert event.operation == "file_change"
                assert event.message == "write notes.txt"
                await adapter.answer_approval("inv-1", event.approval_key, approved=True)
        return seen

    events = asyncio.run(scenario())
    texts = [e.text for e in events if e.kind == "text_final"]
    assert texts[-1] == "change applied"
    assert events[-1].kind == "completed"


def test_declined_approval_reports_the_decline(adapter: CodexAdapter, tmp_path: Path) -> None:
    async def scenario() -> list:
        seen = []
        async for event in adapter.run(_request(tmp_path, "WRITE notes")):
            seen.append(event)
            if event.kind == "approval_request":
                await adapter.answer_approval("inv-1", event.approval_key, approved=False)
        return seen

    events = asyncio.run(scenario())
    assert [e.text for e in events if e.kind == "text_final"][-1] == "change declined"


def test_cancel_stops_a_long_turn(adapter: CodexAdapter, tmp_path: Path) -> None:
    async def scenario() -> list:
        seen = []
        agen = adapter.run(_request(tmp_path, "SLOW please", invocation="inv-slow"))
        async for event in agen:
            seen.append(event)
            if event.kind == "text_delta" and len(seen) > 3:
                await adapter.cancel("inv-slow")
            if event.kind in ("completed", "failed"):
                break
        return seen

    events = asyncio.run(asyncio.wait_for(scenario(), timeout=60))
    assert events[-1].kind in ("completed", "failed")


def test_missing_binary_is_a_clean_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    adapter = CodexAdapter()
    events = asyncio.run(
        _collect(
            adapter.run(
                RunRequest(
                    invocation_id="x",
                    prompt="p",
                    cwd=tmp_path,
                    allowed_tools=[],
                    harness_bin=str(tmp_path / "nope.exe"),
                )
            )
        )
    )
    assert events[0].kind == "failed"
    assert events[0].code == "harness_unavailable"


def test_discover_reports_missing_cli(tmp_path: Path) -> None:
    status = CodexAdapter().discover(str(tmp_path / "missing-codex"))
    assert status.available is False
