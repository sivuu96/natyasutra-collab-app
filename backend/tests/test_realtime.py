"""WebSocket tests: authorisation, replay from sequence, live delivery and origin checks."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from helpers import API, create_project, csrf_headers, general_channel, register
from starlette.websockets import WebSocketDisconnect

ORIGIN = "http://localhost:5173"


@pytest.fixture
def project(client_factory):  # type: ignore[no-untyped-def]
    owner = client_factory()
    register(owner, "rt-owner@example.com", "RT Owner")
    proj = create_project(owner, "Realtime")
    pid = proj["id"]
    return {"owner": owner, "pid": pid, "channel": general_channel(owner, pid)}


def _socket_headers(client: TestClient, origin: str = ORIGIN) -> dict[str, str]:
    return {"origin": origin, "cookie": f"ns_session={client.cookies['ns_session']}"}


def _post(client: TestClient, pid: str, channel: str, body: str) -> None:
    response = client.post(
        f"{API}/projects/{pid}/channels/{channel}/messages", json={"body": body}, headers=csrf_headers(client)
    )
    assert response.status_code == 201


def test_socket_replays_then_streams_live_events(project, app: FastAPI) -> None:  # type: ignore[no-untyped-def]
    owner, pid, ch = project["owner"], project["pid"], project["channel"]
    _post(owner, pid, ch, "before connecting")

    with TestClient(app).websocket_connect(f"/api/v1/ws/projects/{pid}?since=0", headers=_socket_headers(owner)) as ws:
        replayed = ws.receive_json()
        assert replayed["event_type"] == "conversation.message.created"
        assert replayed["sequence"] == 1
        assert replayed["schema_version"] == 1

        _post(owner, pid, ch, "live message")
        live = ws.receive_json()
        assert live["event_type"] == "conversation.message.created"
        assert live["sequence"] == 2
        assert live["payload"]["channel_id"] == ch


def test_since_skips_already_seen_events(project, app: FastAPI) -> None:  # type: ignore[no-untyped-def]
    owner, pid, ch = project["owner"], project["pid"], project["channel"]
    _post(owner, pid, ch, "one")
    _post(owner, pid, ch, "two")
    with TestClient(app).websocket_connect(f"/api/v1/ws/projects/{pid}?since=1", headers=_socket_headers(owner)) as ws:
        replayed = ws.receive_json()
        assert replayed["sequence"] == 2


def test_wrong_origin_rejected(project, app: FastAPI) -> None:  # type: ignore[no-untyped-def]
    owner, pid = project["owner"], project["pid"]
    with (
        pytest.raises(WebSocketDisconnect),
        TestClient(app).websocket_connect(
            f"/api/v1/ws/projects/{pid}", headers=_socket_headers(owner, origin="https://evil.example")
        ) as ws,
    ):
        ws.receive_json()


def test_unauthenticated_socket_rejected(project, app: FastAPI) -> None:  # type: ignore[no-untyped-def]
    pid = project["pid"]
    with (
        pytest.raises(WebSocketDisconnect),
        TestClient(app).websocket_connect(f"/api/v1/ws/projects/{pid}", headers={"origin": ORIGIN}) as ws,
    ):
        ws.receive_json()


def test_sequence_is_per_project(project, client_factory, app: FastAPI) -> None:  # type: ignore[no-untyped-def]
    owner, pid, ch = project["owner"], project["pid"], project["channel"]
    other = client_factory()
    register(other, "rt-other@example.com", "Other")
    other_pid = create_project(other, "Other realtime")["id"]
    other_ch = general_channel(other, other_pid)
    _post(owner, pid, ch, "a")
    _post(other, other_pid, other_ch, "b")
    _post(owner, pid, ch, "c")
    feed = owner.get(f"{API}/projects/{pid}/activity").json()
    assert [e["sequence"] for e in feed] == [1, 2]
