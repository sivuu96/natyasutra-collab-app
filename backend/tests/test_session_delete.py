"""Deleting a session: its creator, project owners and admins may delete it; everyone else cannot."""

# The world fixture is shared with test_sessions.py, so tests take it by name.
# ruff: noqa: F811

import uuid
from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient
from helpers import API, add_member, csrf_headers, register
from test_sessions import _member_shares_a_session, open_session, send_turn, world  # noqa: F401

from app.models.agents import AgentInvocation
from app.models.enums import InvocationStatus


def _delete(client: TestClient, w: dict[str, Any], session_id: str) -> Any:
    return client.delete(f"{API}/projects/{w['pid']}/sessions/{session_id}", headers=csrf_headers(client))


def _listed(client: TestClient, w: dict[str, Any]) -> list[dict[str, Any]]:
    return client.get(f"{API}/projects/{w['pid']}/sessions").json()


def test_the_creator_can_delete_their_own_private_session(world: dict[str, Any]) -> None:
    session = open_session(world, visibility="private")

    assert _delete(world["owner"], world, session["id"]).status_code == 204

    assert all(item["id"] != session["id"] for item in _listed(world["owner"], world))
    assert world["owner"].get(f"{API}/projects/{world['pid']}/sessions/{session['id']}").status_code == 404
    assert send_turn(world["owner"], world, session["id"], "still there?").status_code == 404


def test_a_project_owner_can_delete_a_session_someone_else_created(world: dict[str, Any]) -> None:
    session = _member_shares_a_session(world, visibility="project")

    assert _delete(world["owner"], world, session["id"]).status_code == 204
    assert all(item["id"] != session["id"] for item in _listed(world["member"], world))


def test_an_admin_can_delete_someone_elses_private_session(
    world: dict[str, Any], client_factory: Callable[[], TestClient]
) -> None:
    session = _member_shares_a_session(world, visibility="private")
    admin = client_factory()
    register(admin, "delete-admin@example.com", "Admin")
    add_member(world["owner"], world["pid"], "delete-admin@example.com", role="admin")

    assert _delete(admin, world, session["id"]).status_code == 204


def test_an_ordinary_member_cannot_delete_a_shared_session_they_did_not_create(
    world: dict[str, Any], client_factory: Callable[[], TestClient]
) -> None:
    session = _member_shares_a_session(world, visibility="project")
    bystander = client_factory()
    register(bystander, "delete-bystander@example.com", "Bystander")
    add_member(world["owner"], world["pid"], "delete-bystander@example.com", role="member")

    assert _delete(bystander, world, session["id"]).status_code == 403


def test_an_ordinary_member_cannot_even_tell_a_private_session_exists(
    world: dict[str, Any], client_factory: Callable[[], TestClient]
) -> None:
    session = _member_shares_a_session(world, visibility="private")
    bystander = client_factory()
    register(bystander, "delete-hidden@example.com", "Hidden")
    add_member(world["owner"], world["pid"], "delete-hidden@example.com", role="member")

    assert _delete(bystander, world, session["id"]).status_code == 404


def test_the_creator_of_a_shared_session_can_delete_it(world: dict[str, Any]) -> None:
    session = _member_shares_a_session(world, visibility="project")

    assert _delete(world["member"], world, session["id"]).status_code == 204


def test_a_session_with_a_turn_in_progress_cannot_be_deleted(world: dict[str, Any]) -> None:
    session = open_session(world)
    assert send_turn(world["owner"], world, session["id"], "working on it").status_code == 201

    response = _delete(world["owner"], world, session["id"])

    assert response.status_code == 409
    assert "turn" in response.json()["detail"]


def test_a_deleted_session_cannot_be_deleted_again(world: dict[str, Any]) -> None:
    session = open_session(world)
    assert _delete(world["owner"], world, session["id"]).status_code == 204

    assert _delete(world["owner"], world, session["id"]).status_code == 404


def test_the_list_shows_who_may_delete_each_session(world: dict[str, Any]) -> None:
    session = _member_shares_a_session(world, visibility="project")

    as_owner = next(item for item in _listed(world["owner"], world) if item["id"] == session["id"])
    as_creator = next(item for item in _listed(world["member"], world) if item["id"] == session["id"])

    assert as_owner["can_delete"] is True
    assert as_creator["can_delete"] is True


def test_runs_of_a_deleted_session_leave_the_runs_list(world: dict[str, Any]) -> None:
    session = open_session(world)
    turn = send_turn(world["owner"], world, session["id"], "one question")
    invocation_id = turn.json()["invocation_id"]
    with world["app"].state.session_factory() as db:
        invocation = db.get(AgentInvocation, uuid.UUID(invocation_id))
        assert invocation is not None
        invocation.status = InvocationStatus.COMPLETED.value
        db.commit()
    runs_before = world["owner"].get(f"{API}/projects/{world['pid']}/invocations").json()
    assert any(run["id"] == invocation_id for run in runs_before)

    assert _delete(world["owner"], world, session["id"]).status_code == 204

    runs_after = world["owner"].get(f"{API}/projects/{world['pid']}/invocations").json()
    assert all(run["id"] != invocation_id for run in runs_after)
