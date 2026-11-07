"""Bridge, routing, invocation lifecycle, approvals, cancellation and revocation — driven over the real API."""

import datetime as dt
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from bridge_helpers import bearer, bridge_token, frame, hello, pair_device, read_until
from fastapi import FastAPI
from fastapi.testclient import TestClient
from helpers import API, add_member, create_project, csrf_headers, general_channel, register
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

from app.db.base import utcnow
from app.models import BridgeDevice, PairingCode

HANDLE = "claude-architecture"


@contextmanager
def connect_bridge(app: FastAPI, token: str) -> Iterator[Any]:
    with TestClient(app).websocket_connect("/api/v1/bridge/ws", headers=bearer(token)) as ws:
        yield ws


def _read_until_closed(ws: Any, limit: int = 50) -> WebSocketDisconnect:
    """Frames queued before a close may arrive first. Read through them to the disconnect."""
    for _ in range(limit):
        try:
            ws.receive_json()
        except WebSocketDisconnect as closed:
            return closed
    raise AssertionError("socket was not closed")


def _ack_for(ws: Any, event_id: str) -> dict[str, Any]:
    for _ in range(20):
        received = ws.receive_json()
        if received.get("type") == "bridge.ack" and received["payload"]["event_id"] == event_id:
            return received
        if received.get("type") == "bridge.error":
            return received
    raise AssertionError("no ack received")


def send_and_ack(ws: Any, frame_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    envelope = frame(frame_type, payload)
    ws.send_json(envelope)
    return _ack_for(ws, envelope["event_id"])


@pytest.fixture
def world(app: FastAPI, client_factory: Callable[[], TestClient]) -> dict[str, Any]:
    owner = client_factory()
    owner_user = register(owner, "agent-owner@example.com", "Owner")
    project = create_project(owner, "Agent Project")
    pid = project["id"]
    member = client_factory()
    register(member, "requester@example.com", "Requester")
    add_member(owner, pid, "requester@example.com", role="member")
    bridge_http = client_factory()
    device_id, secret = pair_device(owner, bridge_http, "Owner laptop")
    return {
        "app": app,
        "owner": owner,
        "owner_user": owner_user,
        "member": member,
        "pid": pid,
        "channel": general_channel(owner, pid),
        "bridge_http": bridge_http,
        "device_id": device_id,
        "secret": secret,
        "token": bridge_token(bridge_http, device_id, secret),
    }


def register_and_attach(w: dict[str, Any], ws: Any) -> str:
    """Bridge says hello, owner attaches the agent to the project. Returns the agent id."""
    ws.send_json(hello((HANDLE, "Claude (architecture)", "claude_code")))
    read_until(ws, "bridge.welcome")
    agents = w["owner"].get(f"{API}/me/agents").json()
    agent_id = next(a["id"] for a in agents if a["handle"] == HANDLE)
    attached = w["owner"].post(
        f"{API}/projects/{w['pid']}/agents", json={"agent_id": agent_id}, headers=csrf_headers(w["owner"])
    )
    assert attached.status_code == 201, attached.text
    return agent_id


def mention(w: dict[str, Any], text: str = "please review the schema", queue: bool = False) -> dict[str, Any]:
    response = w["member"].post(
        f"{API}/projects/{w['pid']}/channels/{w['channel']}/messages",
        json={"body": f"@{HANDLE} {text}", "queue_if_offline": queue},
        headers=csrf_headers(w["member"]),
    )
    assert response.status_code == 201, response.text
    return response.json()


def invocations(w: dict[str, Any]) -> list[dict[str, Any]]:
    return w["owner"].get(f"{API}/projects/{w['pid']}/invocations").json()


# ---- registration and attachment ----------------------------------------------------------------


def test_hello_registers_agent_online_for_its_owner(world: dict[str, Any]) -> None:
    with connect_bridge(world["app"], world["token"]) as ws:
        register_and_attach(world, ws)
        mine = world["owner"].get(f"{API}/me/agents").json()
        assert mine[0]["handle"] == HANDLE and mine[0]["online"] is True
        listed = world["member"].get(f"{API}/projects/{world['pid']}/agents").json()
        assert listed[0]["online"] is True and listed[0]["owner_display_name"] == "Owner"


def test_agent_handle_cannot_be_taken_over_by_another_user(world: dict[str, Any], client_factory) -> None:  # type: ignore[no-untyped-def]
    with connect_bridge(world["app"], world["token"]) as ws:
        register_and_attach(world, ws)
    intruder = client_factory()
    register(intruder, "intruder@example.com", "Intruder")
    intruder_bridge = client_factory()
    device_id, secret = pair_device(intruder, intruder_bridge, "Intruder laptop")
    token = bridge_token(intruder_bridge, device_id, secret)
    with connect_bridge(world["app"], token) as ws:
        ws.send_json(hello((HANDLE, "Stolen", "claude_code")))
        refusal = read_until(ws, "bridge.error")
        assert refusal["payload"]["code"] == "agent_registration_refused"
    assert intruder.get(f"{API}/me/agents").json() == []
    owner_view = world["owner"].get(f"{API}/me/agents").json()
    assert owner_view[0]["display_name"] == "Claude (architecture)"


def test_only_the_agent_owner_or_an_admin_can_attach(world: dict[str, Any]) -> None:
    with connect_bridge(world["app"], world["token"]) as ws:
        agent_id = register_and_attach(world, ws)
    # Detach first so the member's attempt is a real attach, not an idempotent no-op.
    world["owner"].delete(f"{API}/projects/{world['pid']}/agents/{agent_id}", headers=csrf_headers(world["owner"]))
    attempt = world["member"].post(
        f"{API}/projects/{world['pid']}/agents", json={"agent_id": agent_id}, headers=csrf_headers(world["member"])
    )
    assert attempt.status_code == 403


def test_non_member_cannot_see_project_agents(world: dict[str, Any], client_factory) -> None:  # type: ignore[no-untyped-def]
    with connect_bridge(world["app"], world["token"]) as ws:
        register_and_attach(world, ws)
    outsider = client_factory()
    register(outsider, "outsider@example.com", "Outsider")
    assert outsider.get(f"{API}/projects/{world['pid']}/agents").status_code == 404
    assert outsider.get(f"{API}/projects/{world['pid']}/invocations").status_code == 404


# ---- routing and streaming ----------------------------------------------------------------------


def test_mention_reaches_owner_bridge_and_reply_streams_back(world: dict[str, Any]) -> None:
    with connect_bridge(world["app"], world["token"]) as ws:
        register_and_attach(world, ws)
        posted = mention(world)
        created = read_until(ws, "invocation.created")
        payload = created["payload"]
        assert payload["agent_handle"] == HANDLE
        assert payload["requested_by"] == "Requester"
        assert "please review the schema" in payload["prompt"]
        assert "project_id" in payload and "channel" not in payload  # no hub-side paths or commands

        invocation_id = payload["invocation_id"]
        assert send_and_ack(ws, "invocation.accepted", {"invocation_id": invocation_id})["type"] == "bridge.ack"
        assert (
            send_and_ack(ws, "invocation.output_delta", {"invocation_id": invocation_id, "text": "Looking"})["type"]
            == "bridge.ack"
        )
        assert (
            send_and_ack(ws, "invocation.output_message", {"invocation_id": invocation_id, "text": "Schema reviewed."})[
                "type"
            ]
            == "bridge.ack"
        )
        assert send_and_ack(ws, "invocation.completed", {"invocation_id": invocation_id})["type"] == "bridge.ack"

    messages = world["member"].get(f"{API}/projects/{world['pid']}/channels/{world['channel']}/messages").json()
    reply = next(m for m in messages if m["body"] == "Schema reviewed.")
    assert reply["author_agent_handle"] == HANDLE
    assert reply["author_display_name"] == "Owner"
    assert reply["parent_message_id"] == posted["id"]

    final = next(i for i in invocations(world) if i["id"] == invocation_id)
    assert final["status"] == "completed"

    events = world["owner"].get(f"{API}/projects/{world['pid']}/activity", params={"limit": 500}).json()
    types = [e["event_type"] for e in events]
    assert "agent.invocation.completed" in types
    assert "agent.output.delta" not in types  # live-only: never persisted


def test_offline_agent_is_reported_as_offline_never_as_running(world: dict[str, Any]) -> None:
    with connect_bridge(world["app"], world["token"]) as ws:
        register_and_attach(world, ws)
    mention(world)
    record = invocations(world)[0]
    assert record["status"] == "rejected"
    assert record["error_code"] == "agent_offline"


def test_queue_if_offline_delivers_when_the_bridge_returns(world: dict[str, Any]) -> None:
    with connect_bridge(world["app"], world["token"]) as ws:
        register_and_attach(world, ws)
    mention(world, queue=True)
    assert invocations(world)[0]["status"] == "queued"
    with connect_bridge(world["app"], world["token"]) as ws:
        ws.send_json(hello((HANDLE, "Claude (architecture)", "claude_code")))
        created = read_until(ws, "invocation.created")
        assert created["payload"]["agent_handle"] == HANDLE
    assert invocations(world)[0]["status"] == "delivered"


def test_cancelling_queued_work_is_immediate(world: dict[str, Any]) -> None:
    with connect_bridge(world["app"], world["token"]) as ws:
        register_and_attach(world, ws)
    mention(world, queue=True)
    invocation_id = invocations(world)[0]["id"]
    response = world["member"].post(
        f"{API}/projects/{world['pid']}/invocations/{invocation_id}/cancel", headers=csrf_headers(world["member"])
    )
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


def test_unattached_agent_mention_is_plain_text(world: dict[str, Any]) -> None:
    with connect_bridge(world["app"], world["token"]) as ws:
        ws.send_json(hello((HANDLE, "Claude (architecture)", "claude_code")))
        read_until(ws, "bridge.welcome")
        mention(world)
    assert invocations(world) == []


# ---- approvals -----------------------------------------------------------------------------------


def test_approval_round_trip(world: dict[str, Any]) -> None:
    with connect_bridge(world["app"], world["token"]) as ws:
        register_and_attach(world, ws)
        mention(world, text="edit the file")
        invocation_id = read_until(ws, "invocation.created")["payload"]["invocation_id"]
        send_and_ack(ws, "invocation.accepted", {"invocation_id": invocation_id})
        send_and_ack(
            ws,
            "approval.required",
            {"invocation_id": invocation_id, "operation": "tool_use", "tools": ["Edit"], "detail": "to fix a typo"},
        )
        assert invocations(world)[0]["status"] == "awaiting_approval"

        pending = world["owner"].get(f"{API}/projects/{world['pid']}/approvals").json()
        assert pending[0]["tools"] == ["Edit"]
        approval_id = pending[0]["id"]

        # The requester cannot approve their own request.
        denied = world["member"].post(
            f"{API}/projects/{world['pid']}/approvals/{approval_id}",
            json={"decision": "approved"},
            headers=csrf_headers(world["member"]),
        )
        assert denied.status_code == 403

        decided = world["owner"].post(
            f"{API}/projects/{world['pid']}/approvals/{approval_id}",
            json={"decision": "approved"},
            headers=csrf_headers(world["owner"]),
        )
        assert decided.status_code == 200 and decided.json()["status"] == "approved"

        resolved = read_until(ws, "approval.resolved")
        assert resolved["payload"]["decision"] == "approved"
        assert resolved["payload"]["invocation_id"] == invocation_id

        again = world["owner"].post(
            f"{API}/projects/{world['pid']}/approvals/{approval_id}",
            json={"decision": "denied"},
            headers=csrf_headers(world["owner"]),
        )
        assert again.status_code == 409


def test_disconnect_while_awaiting_approval_ends_the_run_and_denies_the_approval(world: dict[str, Any]) -> None:
    with connect_bridge(world["app"], world["token"]) as ws:
        register_and_attach(world, ws)
        mention(world, text="edit the file")
        invocation_id = read_until(ws, "invocation.created")["payload"]["invocation_id"]
        send_and_ack(ws, "invocation.accepted", {"invocation_id": invocation_id})
        send_and_ack(
            ws, "approval.required", {"invocation_id": invocation_id, "operation": "tool_use", "tools": ["Edit"]}
        )
        approval_id = world["owner"].get(f"{API}/projects/{world['pid']}/approvals").json()[0]["id"]
    # The bridge went away while the run waited, so the run is over: the approval is denied automatically.
    assert next(i for i in invocations(world) if i["id"] == invocation_id)["status"] == "failed"
    assert world["owner"].get(f"{API}/projects/{world['pid']}/approvals").json() == []
    late = world["owner"].post(
        f"{API}/projects/{world['pid']}/approvals/{approval_id}",
        json={"decision": "approved"},
        headers=csrf_headers(world["owner"]),
    )
    assert late.status_code == 409


# ---- cancellation --------------------------------------------------------------------------------


def test_cancelling_running_work_asks_the_bridge_to_stop(world: dict[str, Any]) -> None:
    with connect_bridge(world["app"], world["token"]) as ws:
        register_and_attach(world, ws)
        mention(world)
        invocation_id = read_until(ws, "invocation.created")["payload"]["invocation_id"]
        send_and_ack(ws, "invocation.accepted", {"invocation_id": invocation_id})
        send_and_ack(ws, "invocation.output_delta", {"invocation_id": invocation_id, "text": "..."})
        response = world["member"].post(
            f"{API}/projects/{world['pid']}/invocations/{invocation_id}/cancel", headers=csrf_headers(world["member"])
        )
        assert response.status_code == 200
        assert read_until(ws, "invocation.cancel")["payload"]["invocation_id"] == invocation_id
        send_and_ack(ws, "invocation.cancelled", {"invocation_id": invocation_id})
    assert next(i for i in invocations(world) if i["id"] == invocation_id)["status"] == "cancelled"


# ---- isolation and integrity --------------------------------------------------------------------


def test_a_device_cannot_change_another_devices_invocation(world: dict[str, Any], client_factory) -> None:  # type: ignore[no-untyped-def]
    # A second device, owned by the same user, tries to claim the first device's invocation while the first is online.
    second_http = client_factory()
    device_id, secret = pair_device(world["owner"], second_http, "Second laptop")
    second_token = bridge_token(second_http, device_id, secret)
    with connect_bridge(world["app"], world["token"]) as ws, connect_bridge(world["app"], second_token) as intruder:
        register_and_attach(world, ws)
        intruder.send_json(hello(("codex-backend", "Codex", "codex")))
        read_until(intruder, "bridge.welcome")
        mention(world)
        invocation_id = read_until(ws, "invocation.created")["payload"]["invocation_id"]

        rejected = send_and_ack(intruder, "invocation.accepted", {"invocation_id": invocation_id})
        assert rejected["type"] == "bridge.error"
        assert rejected["payload"]["code"] == "frame_rejected"
        # The owning device's work is untouched by the attempt.
        assert next(i for i in invocations(world) if i["id"] == invocation_id)["status"] == "delivered"
        assert send_and_ack(ws, "invocation.accepted", {"invocation_id": invocation_id})["type"] == "bridge.ack"


def test_duplicate_frames_are_acknowledged_but_not_reapplied(world: dict[str, Any]) -> None:
    with connect_bridge(world["app"], world["token"]) as ws:
        register_and_attach(world, ws)
        mention(world)
        invocation_id = read_until(ws, "invocation.created")["payload"]["invocation_id"]
        envelope = frame("invocation.accepted", {"invocation_id": invocation_id})
        ws.send_json(envelope)
        first = _ack_for(ws, envelope["event_id"])
        ws.send_json(envelope)
        second = _ack_for(ws, envelope["event_id"])
    assert first["payload"]["duplicate"] is False
    assert second["payload"]["duplicate"] is True


def test_malformed_and_unknown_frames_get_errors_not_crashes(world: dict[str, Any]) -> None:
    with connect_bridge(world["app"], world["token"]) as ws:
        ws.send_json({"type": "shell.exec", "event_id": str(uuid.uuid4()), "payload": {"cmd": "rm"}})
        assert read_until(ws, "bridge.error")["payload"]["code"] == "frame_invalid"
        ws.send_json({"type": "invocation.accepted", "event_id": str(uuid.uuid4()), "payload": {}})
        assert read_until(ws, "bridge.error")["payload"]["code"] == "frame_invalid"
        ws.send_text("not json")
        assert read_until(ws, "bridge.error")["payload"]["code"] == "malformed_json"


def test_invalid_bridge_token_cannot_connect(world: dict[str, Any]) -> None:
    with pytest.raises(WebSocketDisconnect) as closed, connect_bridge(world["app"], "forged.token.value"):
        pass
    assert closed.value.code == 1008


def test_bridge_socket_ignores_session_cookies(world: dict[str, Any]) -> None:
    """A logged-in browser cookie must not authenticate the bridge channel (no ambient authority)."""
    session = world["owner"].cookies["ns_session"]
    with (
        pytest.raises(WebSocketDisconnect),
        TestClient(world["app"]).websocket_connect("/api/v1/bridge/ws", headers={"cookie": f"ns_session={session}"}),
    ):
        pass


# ---- revocation ----------------------------------------------------------------------------------


def test_revoking_a_device_disconnects_it_and_fails_its_active_work(world: dict[str, Any]) -> None:
    with connect_bridge(world["app"], world["token"]) as ws:
        register_and_attach(world, ws)
        mention(world)
        invocation_id = read_until(ws, "invocation.created")["payload"]["invocation_id"]
        send_and_ack(ws, "invocation.accepted", {"invocation_id": invocation_id})
        revoked = world["owner"].delete(f"{API}/devices/{world['device_id']}", headers=csrf_headers(world["owner"]))
        assert revoked.status_code == 204
        closed = _read_until_closed(ws)
        assert closed.code == 4003

    final = next(i for i in invocations(world) if i["id"] == invocation_id)
    assert final["status"] == "failed"
    assert final["error_code"] == "bridge_disconnected"
    assert world["owner"].get(f"{API}/me/agents").json()[0]["revoked"] is True
    assert (
        world["bridge_http"]
        .post(f"{API}/bridge/token", json={"device_id": world["device_id"], "device_secret": world["secret"]})
        .status_code
        == 401
    )
    with pytest.raises(WebSocketDisconnect), connect_bridge(world["app"], world["token"]):
        pass


def test_other_users_cannot_revoke_my_device(world: dict[str, Any], client_factory) -> None:  # type: ignore[no-untyped-def]
    other = client_factory()
    register(other, "other-owner@example.com", "Other")
    response = other.delete(f"{API}/devices/{world['device_id']}", headers=csrf_headers(other))
    assert response.status_code == 404


# ---- pairing -------------------------------------------------------------------------------------


def test_pairing_codes_are_single_use(world: dict[str, Any]) -> None:
    code = world["owner"].post(f"{API}/devices/pairing-codes", headers=csrf_headers(world["owner"])).json()["code"]
    first = world["bridge_http"].post(f"{API}/bridge/pair", json={"code": code, "device_name": "A"})
    assert first.status_code == 201
    second = world["bridge_http"].post(f"{API}/bridge/pair", json={"code": code, "device_name": "B"})
    assert second.status_code == 400


def test_expired_pairing_code_is_refused(world: dict[str, Any]) -> None:
    code = world["owner"].post(f"{API}/devices/pairing-codes", headers=csrf_headers(world["owner"])).json()["code"]
    session_factory = world["app"].state.session_factory
    with session_factory() as db:
        row = db.scalar(select(PairingCode).order_by(PairingCode.created_at.desc()))
        assert row is not None
        row.expires_at = utcnow() - dt.timedelta(seconds=1)
        db.commit()
    response = world["bridge_http"].post(f"{API}/bridge/pair", json={"code": code, "device_name": "Late"})
    assert response.status_code == 400


def test_device_secret_is_stored_only_as_hash(world: dict[str, Any]) -> None:
    session_factory = world["app"].state.session_factory
    with session_factory() as db:
        device = db.get(BridgeDevice, uuid.UUID(world["device_id"]))
        assert device is not None
        assert device.secret_hash != world["secret"]
        assert world["secret"] not in device.secret_hash


def test_token_exchange_rejects_wrong_secret(world: dict[str, Any]) -> None:
    response = world["bridge_http"].post(
        f"{API}/bridge/token", json={"device_id": world["device_id"], "device_secret": "x" * 30}
    )
    assert response.status_code == 401


def test_pairing_code_is_not_stored_in_plaintext(world: dict[str, Any]) -> None:
    code = world["owner"].post(f"{API}/devices/pairing-codes", headers=csrf_headers(world["owner"])).json()["code"]
    session_factory = world["app"].state.session_factory
    with session_factory() as db:
        stored = [row.code_hash for row in db.scalars(select(PairingCode)).all()]
    assert code.replace("-", "") not in stored
    assert all(len(value) == 64 for value in stored)


def test_bridge_endpoints_are_exempt_from_cookie_csrf_only(world: dict[str, Any]) -> None:
    # Pairing is reachable without cookies (it is a bridge call), but account actions still need CSRF.
    no_csrf = TestClient(world["app"]).post(f"{API}/devices/pairing-codes")
    assert no_csrf.status_code == 403
