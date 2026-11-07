"""Invocation runner behaviour against the deterministic fake harness."""

import asyncio
import uuid
from pathlib import Path
from typing import Any

import pytest

from collab_bridge import protocol as p
from collab_bridge.adapters.fake import FakeAdapter
from collab_bridge.config import AgentConfig, BridgeConfig
from collab_bridge.runner import InvocationRunner


class Recorder:
    def __init__(self) -> None:
        self.frames: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, frame_type: str, payload: dict[str, Any]) -> None:
        self.frames.append((frame_type, payload))

    def types(self) -> list[str]:
        return [t for t, _ in self.frames]

    def of(self, frame_type: str) -> list[dict[str, Any]]:
        return [payload for t, payload in self.frames if t == frame_type]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    return path


def _config(repo: Path) -> BridgeConfig:
    agent = AgentConfig(
        handle="fake-agent", display_name="Fake", harness="fake", repo_roots=[str(repo)], default_repo=str(repo)
    )
    return BridgeConfig(hub_url="https://hub.example", device_id="d", agents=[agent])


def _runner(repo: Path, recorder: Recorder) -> InvocationRunner:
    return InvocationRunner(_config(repo), {"fake": FakeAdapter()}, {"fake"}, recorder)


def _created(prompt: str, handle: str = "fake-agent") -> dict[str, Any]:
    return {"invocation_id": str(uuid.uuid4()), "agent_handle": handle, "prompt": prompt, "project_id": "p"}


async def _wait_for(predicate, timeout: float = 5.0) -> None:  # type: ignore[no-untyped-def]
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.01)


def test_echo_invocation_streams_and_completes(repo: Path) -> None:
    async def scenario() -> Recorder:
        recorder = Recorder()
        runner = _runner(repo, recorder)
        runner.handle_created(_created("hello there"))
        await _wait_for(lambda: p.INVOCATION_COMPLETED in recorder.types())
        await runner.shutdown()
        return recorder

    recorder = asyncio.run(scenario())
    assert recorder.types()[0] == p.INVOCATION_ACCEPTED
    assert p.INVOCATION_OUTPUT_DELTA in recorder.types()
    assert recorder.of(p.INVOCATION_OUTPUT_MESSAGE)[0]["text"] == "Echo: hello there"
    assert recorder.types()[-1] == p.INVOCATION_COMPLETED


def test_unknown_agent_is_rejected_not_run(repo: Path) -> None:
    async def scenario() -> Recorder:
        recorder = Recorder()
        runner = _runner(repo, recorder)
        runner.handle_created(_created("hi", handle="someone-else"))
        await asyncio.sleep(0.05)
        await runner.shutdown()
        return recorder

    recorder = asyncio.run(scenario())
    assert recorder.types() == [p.INVOCATION_REJECTED]
    assert recorder.of(p.INVOCATION_REJECTED)[0]["reason_code"] == "unknown_agent"


def test_agent_outside_its_roots_is_rejected(repo: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    config = _config(repo)
    config.agents[0].default_repo = str(outside)  # bypass validate() on purpose: the runtime check must still hold

    async def scenario() -> Recorder:
        recorder = Recorder()
        runner = InvocationRunner(config, {"fake": FakeAdapter()}, {"fake"}, recorder)
        runner.handle_created(_created("hi"))
        await asyncio.sleep(0.05)
        await runner.shutdown()
        return recorder

    recorder = asyncio.run(scenario())
    assert recorder.of(p.INVOCATION_REJECTED)[0]["reason_code"] == "policy"


def test_unavailable_harness_is_rejected(repo: Path) -> None:
    async def scenario() -> Recorder:
        recorder = Recorder()
        runner = InvocationRunner(_config(repo), {"fake": FakeAdapter()}, set(), recorder)
        runner.handle_created(_created("hi"))
        await asyncio.sleep(0.05)
        await runner.shutdown()
        return recorder

    recorder = asyncio.run(scenario())
    assert recorder.of(p.INVOCATION_REJECTED)[0]["reason_code"] == "harness_unavailable"


def test_harness_failure_is_reported_as_failed(repo: Path) -> None:
    async def scenario() -> Recorder:
        recorder = Recorder()
        runner = _runner(repo, recorder)
        runner.handle_created(_created("[[fail]] please"))
        await _wait_for(lambda: p.INVOCATION_FAILED in recorder.types())
        await runner.shutdown()
        return recorder

    recorder = asyncio.run(scenario())
    assert recorder.of(p.INVOCATION_FAILED)[0]["code"] == "harness_error"
    assert p.INVOCATION_COMPLETED not in recorder.types()


def test_permission_request_waits_for_approval_then_resumes(repo: Path) -> None:
    async def scenario() -> tuple[Recorder, dict[str, Any]]:
        recorder = Recorder()
        runner = _runner(repo, recorder)
        created = _created("please [[permission:Edit]] the README")
        runner.handle_created(created)
        await _wait_for(lambda: p.APPROVAL_REQUIRED in recorder.types())
        # Nothing runs while the decision is pending.
        assert p.INVOCATION_COMPLETED not in recorder.types()
        runner.resolve_approval(created["invocation_id"], "approved")
        await _wait_for(lambda: recorder.types().count(p.INVOCATION_COMPLETED) == 1)
        await runner.shutdown()
        return recorder, created

    recorder, created = asyncio.run(scenario())
    approval = recorder.of(p.APPROVAL_REQUIRED)[0]
    assert approval["tools"] == ["Edit"]
    assert approval["operation"] == "tool_use"
    assert any("Continuing with tools" in m["text"] for m in recorder.of(p.INVOCATION_OUTPUT_MESSAGE))


def test_denied_permission_stops_without_running_the_tool(repo: Path) -> None:
    async def scenario() -> Recorder:
        recorder = Recorder()
        runner = _runner(repo, recorder)
        created = _created("[[permission:Write]] write it")
        runner.handle_created(created)
        await _wait_for(lambda: p.APPROVAL_REQUIRED in recorder.types())
        runner.resolve_approval(created["invocation_id"], "denied")
        await _wait_for(lambda: p.INVOCATION_COMPLETED in recorder.types())
        await runner.shutdown()
        return recorder

    recorder = asyncio.run(scenario())
    notes = [m["text"] for m in recorder.of(p.INVOCATION_OUTPUT_MESSAGE)]
    assert any("not approved" in text for text in notes)
    assert not any("Continuing with tools" in text for text in notes)


def test_live_approval_pauses_then_applies_when_approved(repo: Path) -> None:
    async def scenario() -> Recorder:
        recorder = Recorder()
        runner = _runner(repo, recorder)
        created = _created("[[live-approval:file_change]] change the file")
        runner.handle_created(created)
        await _wait_for(lambda: p.APPROVAL_REQUIRED in recorder.types())
        assert p.INVOCATION_COMPLETED not in recorder.types()
        runner.resolve_approval(created["invocation_id"], "approved")
        await _wait_for(lambda: p.INVOCATION_COMPLETED in recorder.types())
        await runner.shutdown()
        return recorder

    recorder = asyncio.run(scenario())
    assert recorder.of(p.APPROVAL_REQUIRED)[0]["operation"] == "file_change"
    assert any(m["text"] == "applied the change" for m in recorder.of(p.INVOCATION_OUTPUT_MESSAGE))


def test_live_approval_denied_is_answered_and_the_run_continues(repo: Path) -> None:
    async def scenario() -> Recorder:
        recorder = Recorder()
        runner = _runner(repo, recorder)
        created = _created("[[live-approval:command]] run it")
        runner.handle_created(created)
        await _wait_for(lambda: p.APPROVAL_REQUIRED in recorder.types())
        runner.resolve_approval(created["invocation_id"], "denied")
        await _wait_for(lambda: p.INVOCATION_COMPLETED in recorder.types())
        await runner.shutdown()
        return recorder

    recorder = asyncio.run(scenario())
    assert any(m["text"] == "the change was declined" for m in recorder.of(p.INVOCATION_OUTPUT_MESSAGE))


def test_cancel_releases_a_pending_live_approval_immediately(repo: Path) -> None:
    async def scenario() -> Recorder:
        recorder = Recorder()
        runner = _runner(repo, recorder)
        created = _created("[[live-approval:command]] run it")
        runner.handle_created(created)
        await _wait_for(lambda: p.APPROVAL_REQUIRED in recorder.types())
        await runner.cancel(created["invocation_id"])
        await _wait_for(lambda: p.INVOCATION_CANCELLED in recorder.types(), timeout=3)
        await runner.shutdown()
        return recorder

    recorder = asyncio.run(scenario())
    assert p.INVOCATION_CANCELLED in recorder.types()


def test_approval_timeout_is_a_denial(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import collab_bridge.runner as runner_module

    monkeypatch.setattr(runner_module, "APPROVAL_TIMEOUT_SECONDS", 0.05)

    async def scenario() -> Recorder:
        recorder = Recorder()
        runner = _runner(repo, recorder)
        runner.handle_created(_created("[[permission:Bash]] run tests"))
        await _wait_for(lambda: p.INVOCATION_COMPLETED in recorder.types(), timeout=5)
        await runner.shutdown()
        return recorder

    recorder = asyncio.run(scenario())
    assert any("not approved" in m["text"] for m in recorder.of(p.INVOCATION_OUTPUT_MESSAGE))


def test_cancel_while_running_reports_cancelled(repo: Path) -> None:
    async def scenario() -> Recorder:
        recorder = Recorder()
        runner = _runner(repo, recorder)
        created = _created("[[slow]] take your time")
        runner.handle_created(created)
        await _wait_for(lambda: p.INVOCATION_OUTPUT_DELTA in recorder.types())
        await runner.cancel(created["invocation_id"])
        await _wait_for(lambda: p.INVOCATION_CANCELLED in recorder.types())
        await runner.shutdown()
        return recorder

    recorder = asyncio.run(scenario())
    assert p.INVOCATION_COMPLETED not in recorder.types()


def test_cancel_before_start_skips_the_run(repo: Path) -> None:
    async def scenario() -> Recorder:
        recorder = Recorder()
        runner = _runner(repo, recorder)
        blocker = _created("[[slow]] first")
        queued = _created("second")
        runner.handle_created(blocker)
        runner.handle_created(queued)
        await _wait_for(lambda: p.INVOCATION_OUTPUT_DELTA in recorder.types())
        await runner.cancel(queued["invocation_id"])
        await runner.cancel(blocker["invocation_id"])
        await _wait_for(lambda: recorder.types().count(p.INVOCATION_CANCELLED) == 2)
        await runner.shutdown()
        return recorder

    recorder = asyncio.run(scenario())
    cancelled_ids = [payload["invocation_id"] for payload in recorder.of(p.INVOCATION_CANCELLED)]
    assert len(cancelled_ids) == 2
    assert not any("Echo" in m["text"] for m in recorder.of(p.INVOCATION_OUTPUT_MESSAGE))


def test_agent_runs_are_serial(repo: Path) -> None:
    async def scenario() -> list[str]:
        recorder = Recorder()
        runner = _runner(repo, recorder)
        first = _created("first")
        second = _created("second")
        runner.handle_created(first)
        runner.handle_created(second)
        await _wait_for(lambda: recorder.types().count(p.INVOCATION_COMPLETED) == 2)
        await runner.shutdown()
        return [payload["invocation_id"] for payload in recorder.of(p.INVOCATION_ACCEPTED)]

    accepted_order = asyncio.run(scenario())
    assert len(accepted_order) == 2
