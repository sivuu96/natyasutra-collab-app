"""Dedicated agent sessions: turns, harness resume, privacy, sharing and approvals."""

from collections.abc import Callable
from typing import Any

import pytest
from bridge_helpers import bridge_token, frame, hello, pair_device, read_until
from fastapi import FastAPI
from fastapi.testclient import TestClient
from helpers import API, add_member, create_project, csrf_headers, general_channel, register
from test_bridge_flow import connect_bridge, register_and_attach, send_and_ack

HANDLE = "claude-architecture"


@pytest.fixture
def world(app: FastAPI, client_factory: Callable[[], TestClient]) -> dict[str, Any]:
    owner = client_factory()
    owner_user = register(owner, "session-owner@example.com", "Owner")
    project = create_project(owner, "Session Project")
    pid = project["id"]
    member = client_factory()
    member_user = register(member, "session-member@example.com", "Member")
    add_member(owner, pid, "session-member@example.com", role="member")
    bridge_http = client_factory()
    device_id, secret = pair_device(owner, bridge_http, "Owner laptop")
    w = {
        "app": app,
        "owner": owner,
        "owner_user": owner_user,
        "member": member,
        "member_user": member_user,
        "pid": pid,
        "channel": general_channel(owner, pid),
        "token": bridge_token(bridge_http, device_id, secret),
    }
    # The agent is registered and attached once; later bridge connections are plain sessions.
    with connect_bridge(app, w["token"]) as ws:
        register_and_attach(w, ws)
    return w


def _agent_id(w: dict[str, Any]) -> str:
    return next(a["id"] for a in w["owner"].get(f"{API}/me/agents").json() if a["handle"] == HANDLE)


def open_session(w: dict[str, Any], visibility: str = "private", who: str = "owner") -> dict[str, Any]:
    client = w[who]
    response = client.post(
        f"{API}/projects/{w['pid']}/sessions",
        headers=csrf_headers(client),
        json={"agent_id": _agent_id(w), "title": "Review the schema", "visibility": visibility},
    )
    assert response.status_code == 201, response.text
    return response.json()


def turn(w: dict[str, Any], session_id: str, body: str, who: str = "owner", queue: bool = False):  # type: ignore[no-untyped-def]
    client = w[who]
    return client.post(
        f"{API}/projects/{w['pid']}/sessions/{session_id}/turns",
        headers=csrf_headers(client),
        json={"body": body, "queue_if_offline": queue},
    )


def answer_turn(ws: Any, invocation_id: str, text: str, harness_ref: str | None = "ses-1") -> None:
    send_and_ack(ws, "invocation.accepted", {"invocation_id": invocation_id})
    send_and_ack(ws, "invocation.output_delta", {"invocation_id": invocation_id, "text": "thinking"})
    send_and_ack(ws, "invocation.output_message", {"invocation_id": invocation_id, "text": text})
    send_and_ack(ws, "invocation.completed", {"invocation_id": invocation_id, "harness_session_ref": harness_ref})


# ---- creation and turns --------------------------------------------------------------------------


def test_session_requires_an_attached_agent(world: dict[str, Any]) -> None:
    response = world["owner"].post(
        f"{API}/projects/{world['pid']}/sessions",
        headers=csrf_headers(world["owner"]),
        json={"agent_id": "00000000-0000-0000-0000-000000000000", "title": "x"},
    )
    assert response.status_code == 404


def test_turn_reaches_the_bridge_and_the_reply_lands_in_the_transcript(world: dict[str, Any]) -> None:
    with connect_bridge(world["app"], world["token"]) as ws:
        register_and_attach(world, ws)
        session = open_session(world)
        posted = turn(world, session["id"], "Please review the schema.")
        assert posted.status_code == 201, posted.text
        created = read_until(ws, "invocation.created")["payload"]
        assert created["prompt"] == "Please review the schema."
        assert created.get("harness_session_ref") is None
        answer_turn(ws, created["invocation_id"], "Looks consistent.", harness_ref="ses-1")

    transcript = world["owner"].get(f"{API}/projects/{world['pid']}/sessions/{session['id']}/messages").json()
    assert [m["role"] for m in transcript] == ["user", "agent"]
    assert transcript[1]["body"] == "Looks consistent."
    assert transcript[1]["agent_handle"] == HANDLE
    # The reply did not leak into the channel.
    channel = world["owner"].get(f"{API}/projects/{world['pid']}/channels/{world['channel']}/messages").json()
    assert not any(m["body"] == "Looks consistent." for m in channel)


def test_second_turn_resumes_the_harness_session(world: dict[str, Any]) -> None:
    with connect_bridge(world["app"], world["token"]) as ws:
        register_and_attach(world, ws)
        session = open_session(world)
        turn(world, session["id"], "first")
        first = read_until(ws, "invocation.created")["payload"]
        answer_turn(ws, first["invocation_id"], "one", harness_ref="ses-42")
        turn(world, session["id"], "second")
        second = read_until(ws, "invocation.created")["payload"]
        assert second["harness_session_ref"] == "ses-42"


def test_a_turn_cannot_start_while_another_is_in_progress(world: dict[str, Any]) -> None:
    with connect_bridge(world["app"], world["token"]) as ws:
        register_and_attach(world, ws)
        session = open_session(world)
        assert turn(world, session["id"], "first").status_code == 201
        read_until(ws, "invocation.created")
        assert turn(world, session["id"], "second").status_code == 409


def test_offline_bridge_rejects_the_turn_and_says_so(world: dict[str, Any]) -> None:
    with connect_bridge(world["app"], world["token"]) as ws:
        register_and_attach(world, ws)
        session = open_session(world)
    response = turn(world, session["id"], "anyone there?")
    assert response.status_code == 201
    assert response.json()["status"] == "rejected"
    assert response.json()["error_code"] == "agent_offline"


def test_queued_turn_waits_for_the_bridge(world: dict[str, Any]) -> None:
    with connect_bridge(world["app"], world["token"]) as ws:
        register_and_attach(world, ws)
        session = open_session(world)
    assert turn(world, session["id"], "later", queue=True).json()["status"] == "queued"
    with connect_bridge(world["app"], world["token"]) as ws:
        ws.send_json(hello((HANDLE, "Claude (architecture)", "claude_code")))
        assert read_until(ws, "invocation.created")["payload"]["prompt"] == "later"


def test_closed_session_refuses_turns(world: dict[str, Any]) -> None:
    session = open_session(world)
    world["owner"].post(
        f"{API}/projects/{world['pid']}/sessions/{session['id']}/close", headers=csrf_headers(world["owner"])
    )
    assert turn(world, session["id"], "hello").status_code == 409


def test_owner_can_cancel_a_running_turn(world: dict[str, Any]) -> None:
    with connect_bridge(world["app"], world["token"]) as ws:
        register_and_attach(world, ws)
        session = open_session(world)
        turn(world, session["id"], "long task")
        invocation_id = read_until(ws, "invocation.created")["payload"]["invocation_id"]
        send_and_ack(ws, "invocation.accepted", {"invocation_id": invocation_id})
        response = world["owner"].post(
            f"{API}/projects/{world['pid']}/invocations/{invocation_id}/cancel", headers=csrf_headers(world["owner"])
        )
        assert response.status_code == 200
        assert read_until(ws, "invocation.cancel")["payload"]["invocation_id"] == invocation_id


# ---- privacy --------------------------------------------------------------------------------------


def test_private_session_is_invisible_to_other_members(world: dict[str, Any]) -> None:
    session = open_session(world, visibility="private")
    member = world["member"]
    pid = world["pid"]
    assert member.get(f"{API}/projects/{pid}/sessions/{session['id']}").status_code == 404
    assert member.get(f"{API}/projects/{pid}/sessions/{session['id']}/messages").status_code == 404
    assert (
        member.post(
            f"{API}/projects/{pid}/sessions/{session['id']}/turns", headers=csrf_headers(member), json={"body": "hi"}
        ).status_code
        == 404
    )
    listed = member.get(f"{API}/projects/{pid}/sessions").json()
    assert all(s["id"] != session["id"] for s in listed)


def test_private_session_events_are_hidden_from_other_members_everywhere(world: dict[str, Any]) -> None:
    pid = world["pid"]
    with connect_bridge(world["app"], world["token"]) as bridge:
        register_and_attach(world, bridge)
        session = open_session(world)
        turn(world, session["id"], "secret plans")
        invocation_id = read_until(bridge, "invocation.created")["payload"]["invocation_id"]
        send_and_ack(bridge, "invocation.accepted", {"invocation_id": invocation_id})
        send_and_ack(bridge, "invocation.output_delta", {"invocation_id": invocation_id, "text": "SECRET-DELTA"})
        send_and_ack(bridge, "invocation.output_message", {"invocation_id": invocation_id, "text": "SECRET-REPLY"})
        send_and_ack(bridge, "invocation.completed", {"invocation_id": invocation_id, "harness_session_ref": "s"})

    # The other member's activity feed never shows the session's content or its events.
    feed = world["member"].get(f"{API}/projects/{pid}/activity", params={"limit": 500}).json()
    assert "SECRET-REPLY" not in str(feed) and "SECRET-DELTA" not in str(feed)
    assert all(e["payload"].get("session_id") != session["id"] for e in feed)
    assert all(
        e["payload"].get("visibility") != "private" or e["payload"].get("owner_user_id") == world["owner_user"]["id"]
        for e in feed
    )

    # The owner's feed does include them.
    owner_feed = world["owner"].get(f"{API}/projects/{pid}/activity", params={"limit": 500}).json()
    assert any(e["event_type"] == "session.message.created" for e in owner_feed)

    # A live socket for the other member gets no private events; the next public message is the first frame.
    member_cookie = world["member"].cookies["ns_session"]
    with TestClient(world["app"]).websocket_connect(
        f"/api/v1/ws/projects/{pid}?since=0",
        headers={"origin": "http://localhost:5173", "cookie": f"ns_session={member_cookie}"},
    ) as member_ws:
        public = world["owner"].post(
            f"{API}/projects/{pid}/channels/{world['channel']}/messages",
            headers=csrf_headers(world["owner"]),
            json={"body": "public hello"},
        )
        assert public.status_code == 201
        received = []
        for _ in range(50):
            frame_in = member_ws.receive_json()
            if frame_in.get("event_type") == "realtime.heartbeat":
                continue
            received.append(frame_in)
            if (
                frame_in.get("event_type") == "conversation.message.created"
                and frame_in["payload"].get("message_id") == public.json()["id"]
            ):
                break
        assert received, "member should receive the public message"
        assert all(f.get("visibility") != "private" for f in received)
        assert "SECRET" not in str(received)


def test_sharing_gives_members_read_access_but_not_control(world: dict[str, Any]) -> None:
    session = open_session(world, visibility="private")
    pid = world["pid"]
    shared = world["owner"].post(
        f"{API}/projects/{pid}/sessions/{session['id']}/visibility",
        headers=csrf_headers(world["owner"]),
        json={"visibility": "project"},
    )
    assert shared.status_code == 200 and shared.json()["visibility"] == "project"
    assert world["member"].get(f"{API}/projects/{pid}/sessions/{session['id']}").status_code == 200
    assert world["member"].get(f"{API}/projects/{pid}/sessions/{session['id']}/messages").status_code == 200
    assert turn(world, session["id"], "can I?", who="member").status_code == 403


def test_only_the_owner_can_change_visibility(world: dict[str, Any]) -> None:
    session = open_session(world)
    response = world["member"].post(
        f"{API}/projects/{world['pid']}/sessions/{session['id']}/visibility",
        headers=csrf_headers(world["member"]),
        json={"visibility": "project"},
    )
    assert response.status_code in (403, 404)


def test_private_session_approvals_are_hidden_and_undecidable_by_others(world: dict[str, Any]) -> None:
    pid = world["pid"]
    with connect_bridge(world["app"], world["token"]) as ws:
        register_and_attach(world, ws)
        session = open_session(world)
        turn(world, session["id"], "edit the file")
        invocation_id = read_until(ws, "invocation.created")["payload"]["invocation_id"]
        send_and_ack(ws, "invocation.accepted", {"invocation_id": invocation_id})
        send_and_ack(
            ws,
            "approval.required",
            {
                "invocation_id": invocation_id,
                "operation": "tool_use",
                "tools": ["Edit"],
                "detail": "rm -rf secret-folder",
            },
        )
        owner_list = world["owner"].get(f"{API}/projects/{pid}/approvals").json()
        assert len(owner_list) == 1
        approval_id = owner_list[0]["id"]
        assert world["member"].get(f"{API}/projects/{pid}/approvals").json() == []
        # The member cannot even learn the approval exists.
        other = world["member"].post(
            f"{API}/projects/{pid}/approvals/{approval_id}",
            headers=csrf_headers(world["member"]),
            json={"decision": "approved"},
        )
        assert other.status_code == 404
        # The owner (who runs the machine) can decide.
        decided = world["owner"].post(
            f"{API}/projects/{pid}/approvals/{approval_id}",
            headers=csrf_headers(world["owner"]),
            json={"decision": "denied"},
        )
        assert decided.status_code == 200
        assert read_until(ws, "approval.resolved")["payload"]["decision"] == "denied"


def test_session_invocations_are_hidden_from_other_members(world: dict[str, Any]) -> None:
    with connect_bridge(world["app"], world["token"]) as ws:
        register_and_attach(world, ws)
        session = open_session(world)
        turn(world, session["id"], "private question")
        read_until(ws, "invocation.created")
    pid = world["pid"]
    assert world["member"].get(f"{API}/projects/{pid}/invocations").json() == []
    assert len(world["owner"].get(f"{API}/projects/{pid}/invocations").json()) == 1


def test_frame_for_unknown_session_is_not_a_security_hole(world: dict[str, Any]) -> None:
    # Sanity: a bridge frame naming an invocation that does not exist is rejected, not applied.
    with connect_bridge(world["app"], world["token"]) as ws:
        register_and_attach(world, ws)
        envelope = frame(
            "invocation.output_message", {"invocation_id": "00000000-0000-0000-0000-000000000000", "text": "x"}
        )
        ws.send_json(envelope)
        assert read_until(ws, "bridge.error")["payload"]["code"] == "frame_rejected"
