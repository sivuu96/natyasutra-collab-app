"""Dedicated agent sessions: turns, one turn at a time, privacy, sharing, and closing."""

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from helpers import API, add_member, create_project, csrf_headers, general_channel, register

from app.models.agents import Agent, AgentInvocation, ProjectAgent
from app.models.enums import InvocationStatus

HANDLE = "session-claude"


@pytest.fixture
def world(app: FastAPI, client_factory: Callable[[], TestClient]) -> dict[str, Any]:
    owner = client_factory()
    owner_user = register(owner, "session-owner@example.com", "Owner")
    project = create_project(owner, "Session Project")
    pid = project["id"]
    member = client_factory()
    member_user = register(member, "session-member@example.com", "Member")
    add_member(owner, pid, "session-member@example.com", role="member")
    with app.state.session_factory() as db:
        agent = Agent(
            owner_user_id=uuid.UUID(owner_user["id"]),
            handle=HANDLE,
            display_name="Session Claude",
            harness="claude_code",
        )
        db.add(agent)
        db.flush()
        db.add(ProjectAgent(project_id=uuid.UUID(pid), agent_id=agent.id, added_by_user_id=uuid.UUID(owner_user["id"])))
        db.commit()
        agent_id = str(agent.id)
    return {
        "app": app,
        "owner": owner,
        "member": member,
        "member_user": member_user,
        "pid": pid,
        "channel": general_channel(owner, pid),
        "agent_id": agent_id,
    }


def open_session(w: dict[str, Any], visibility: str = "private") -> dict[str, Any]:
    response = w["owner"].post(
        f"{API}/projects/{w['pid']}/sessions",
        json={"agent_id": w["agent_id"], "title": "Architecture", "visibility": visibility},
        headers=csrf_headers(w["owner"]),
    )
    assert response.status_code == 201, response.text
    return response.json()


def send_turn(client: TestClient, w: dict[str, Any], session_id: str, body: str) -> Any:
    return client.post(
        f"{API}/projects/{w['pid']}/sessions/{session_id}/turns",
        json={"body": body},
        headers=csrf_headers(client),
    )


def test_a_turn_queues_a_run_in_the_session(world: dict[str, Any]) -> None:
    session = open_session(world)

    response = send_turn(world["owner"], world, session["id"], "Review the schema.")

    assert response.status_code == 201, response.text
    assert response.json()["status"] == InvocationStatus.QUEUED.value
    with world["app"].state.session_factory() as db:
        invocation = db.get(AgentInvocation, uuid.UUID(response.json()["invocation_id"]))
        assert invocation is not None
        assert str(invocation.session_id) == session["id"]
        assert invocation.prompt == "Review the schema."


def test_a_second_turn_waits_until_the_first_is_finished(world: dict[str, Any]) -> None:
    session = open_session(world)
    assert send_turn(world["owner"], world, session["id"], "first").status_code == 201

    second = send_turn(world["owner"], world, session["id"], "second")

    assert second.status_code == 409
    assert second.json()["detail"] == "A turn is already in progress"


def test_a_private_session_is_hidden_from_other_members(world: dict[str, Any]) -> None:
    session = open_session(world, visibility="private")

    assert world["member"].get(f"{API}/projects/{world['pid']}/sessions/{session['id']}").status_code == 404
    assert world["member"].get(f"{API}/projects/{world['pid']}/sessions/{session['id']}/messages").status_code == 404
    listed = world["member"].get(f"{API}/projects/{world['pid']}/sessions").json()
    assert all(item["id"] != session["id"] for item in listed)


def test_a_shared_session_is_readable_by_members_but_not_writable(world: dict[str, Any]) -> None:
    session = open_session(world, visibility="project")

    assert world["member"].get(f"{API}/projects/{world['pid']}/sessions/{session['id']}/messages").status_code == 200
    denied = send_turn(world["member"], world, session["id"], "can I write?")
    assert denied.status_code == 403


def test_making_a_session_shared_lets_members_read_it(world: dict[str, Any]) -> None:
    session = open_session(world, visibility="private")

    changed = world["owner"].post(
        f"{API}/projects/{world['pid']}/sessions/{session['id']}/visibility",
        json={"visibility": "project"},
        headers=csrf_headers(world["owner"]),
    )

    assert changed.status_code == 200
    assert world["member"].get(f"{API}/projects/{world['pid']}/sessions/{session['id']}").status_code == 200


def test_a_closed_session_takes_no_turns(world: dict[str, Any]) -> None:
    session = open_session(world)
    closed = world["owner"].post(
        f"{API}/projects/{world['pid']}/sessions/{session['id']}/close", headers=csrf_headers(world["owner"])
    )
    assert closed.status_code == 200

    response = send_turn(world["owner"], world, session["id"], "still there?")

    assert response.status_code == 409
    assert response.json()["detail"] == "This session is closed"


def test_a_member_cannot_close_someone_elses_session(world: dict[str, Any]) -> None:
    session = open_session(world, visibility="project")

    response = world["member"].post(
        f"{API}/projects/{world['pid']}/sessions/{session['id']}/close", headers=csrf_headers(world["member"])
    )

    assert response.status_code == 403


def test_my_agents_lists_only_the_agents_you_own(world: dict[str, Any]) -> None:
    owned = world["owner"].get(f"{API}/me/agents")
    assert owned.status_code == 200, owned.text
    assert [a["handle"] for a in owned.json()] == [HANDLE]
    assert world["member"].get(f"{API}/me/agents").json() == []
