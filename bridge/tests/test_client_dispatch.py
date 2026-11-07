"""Regression: an invocation delivered over the socket must start running, through the client's own dispatch.

A previous version started per-agent workers before their queues existed, so the worker died and
nothing ever ran. This test drives BridgeClient._handle exactly as the socket reader does.
"""

import asyncio
import json
import uuid
from pathlib import Path

from collab_bridge.adapters.fake import FakeAdapter
from collab_bridge.client import BridgeClient
from collab_bridge.config import AgentConfig, BridgeConfig
from collab_bridge.state import BridgeState


def test_invocation_frame_is_accepted_and_completed_through_client_dispatch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    agent = AgentConfig(
        handle="fake-agent", display_name="Fake", harness="fake", repo_roots=[str(repo)], default_repo=str(repo)
    )
    config = BridgeConfig(hub_url="https://hub.example", device_id="d", agents=[agent])
    state = BridgeState(tmp_path / "bridge.sqlite3")
    client = BridgeClient(config, "d", "secret-secret-secret-secret", state, {"fake": FakeAdapter()})

    async def scenario() -> list[str]:
        client._send_queue = asyncio.Queue()
        frame = {
            "type": "invocation.created",
            "event_id": str(uuid.uuid4()),
            "payload": {
                "invocation_id": str(uuid.uuid4()),
                "agent_handle": "fake-agent",
                "prompt": "hello",
                "project_id": str(uuid.uuid4()),
            },
        }
        await client._handle(json.dumps(frame))
        sent: list[str] = []
        for _ in range(100):
            await asyncio.sleep(0.02)
            while not client._send_queue.empty():
                sent.append(client._send_queue.get_nowait()["type"])
            if "invocation.completed" in sent or "invocation.failed" in sent:
                break
        await client._runner.shutdown()
        return sent

    sent = asyncio.run(scenario())
    assert sent[0] == "invocation.accepted"
    assert sent[-1] == "invocation.completed"
    state.close()
