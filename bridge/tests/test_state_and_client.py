import uuid
from pathlib import Path

from collab_bridge import protocol as p
from collab_bridge.client import backoff_delay, websocket_url
from collab_bridge.state import BridgeState


def test_outbox_survives_reopen_and_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "bridge.sqlite3"
    state = BridgeState(path)
    frame = p.envelope(p.INVOCATION_OUTPUT_MESSAGE, {"invocation_id": str(uuid.uuid4()), "text": "hi"})
    state.enqueue(frame)
    state.enqueue(frame)  # same event_id: stored once
    state.close()

    reopened = BridgeState(path)
    pending = reopened.pending()
    assert [f["event_id"] for f in pending] == [frame["event_id"]]
    reopened.acknowledge(frame["event_id"])
    assert reopened.pending() == []
    reopened.close()


def test_outbox_preserves_order(tmp_path: Path) -> None:
    state = BridgeState(tmp_path / "bridge.sqlite3")
    frames = [
        p.envelope(p.INVOCATION_OUTPUT_MESSAGE, {"invocation_id": str(uuid.uuid4()), "text": str(i)}) for i in range(5)
    ]
    for frame in frames:
        state.enqueue(frame)
    assert [f["event_id"] for f in state.pending()] == [f["event_id"] for f in frames]
    state.close()


def test_metadata_store(tmp_path: Path) -> None:
    state = BridgeState(tmp_path / "bridge.sqlite3")
    assert state.get("k") is None
    state.set("k", "v")
    assert state.get("k") == "v"
    state.delete("k")
    assert state.get("k") is None
    state.close()


def test_backoff_is_bounded_and_jittered() -> None:
    samples = [backoff_delay(attempt) for attempt in range(12) for _ in range(20)]
    assert all(0.2 <= s <= 60.0 for s in samples)
    assert len(set(round(s, 3) for s in samples)) > 50  # jittered, not a fixed schedule


def test_websocket_url_scheme_mapping() -> None:
    assert websocket_url("https://hub.example/") == "wss://hub.example/api/v1/bridge/ws"
    assert websocket_url("http://localhost:8000") == "ws://localhost:8000/api/v1/bridge/ws"
