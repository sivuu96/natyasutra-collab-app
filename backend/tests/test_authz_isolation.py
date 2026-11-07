"""Multi-user isolation tests. A user outside a project must not learn anything about it."""

import uuid

import pytest
from fastapi.testclient import TestClient
from helpers import (
    API,
    add_member,
    create_project,
    csrf_headers,
    general_channel,
    register,
)


@pytest.fixture
def world(client_factory):  # type: ignore[no-untyped-def]
    """Owner has a project with a channel, message, task and decision. Outsider is registered but not a member."""
    owner = client_factory()
    register(owner, "owner@example.com", "Owner")
    project = create_project(owner, "Secret Project")
    pid = project["id"]
    channel_id = general_channel(owner, pid)
    message = owner.post(
        f"{API}/projects/{pid}/channels/{channel_id}/messages",
        json={"body": "confidential plan"},
        headers=csrf_headers(owner),
    ).json()
    task = owner.post(
        f"{API}/projects/{pid}/tasks", json={"title": "confidential task"}, headers=csrf_headers(owner)
    ).json()
    decision = owner.post(
        f"{API}/projects/{pid}/decisions",
        json={"title": "confidential decision", "statement": "confidential statement"},
        headers=csrf_headers(owner),
    ).json()
    outsider = client_factory()
    register(outsider, "outsider@example.com", "Outsider")
    return {
        "owner": owner,
        "outsider": outsider,
        "pid": pid,
        "channel": channel_id,
        "message": message["id"],
        "task": task["id"],
        "decision": decision["id"],
    }


def test_non_member_gets_404_on_every_project_read(world) -> None:  # type: ignore[no-untyped-def]
    o, pid = world["outsider"], world["pid"]
    paths = [
        f"{API}/projects/{pid}",
        f"{API}/projects/{pid}/members",
        f"{API}/projects/{pid}/channels",
        f"{API}/projects/{pid}/channels/{world['channel']}/messages",
        f"{API}/projects/{pid}/tasks",
        f"{API}/projects/{pid}/tasks/{world['task']}",
        f"{API}/projects/{pid}/decisions",
        f"{API}/projects/{pid}/decisions/{world['decision']}",
        f"{API}/projects/{pid}/decisions/{world['decision']}/revisions",
        f"{API}/projects/{pid}/activity",
        f"{API}/projects/{pid}/search?q=confidential",
        f"{API}/projects/{pid}/audit",
    ]
    for path in paths:
        response = o.get(path)
        assert response.status_code == 404, f"{path} leaked with {response.status_code}"
        assert "confidential" not in response.text


def test_non_member_gets_404_on_every_project_write(world) -> None:  # type: ignore[no-untyped-def]
    o, pid = world["outsider"], world["pid"]
    h = csrf_headers(o)
    writes = [
        ("post", f"{API}/projects/{pid}/channels/{world['channel']}/messages", {"body": "intrusion"}),
        ("patch", f"{API}/projects/{pid}/messages/{world['message']}", {"body": "tampered"}),
        ("delete", f"{API}/projects/{pid}/messages/{world['message']}", None),
        ("post", f"{API}/projects/{pid}/tasks", {"title": "intrusion"}),
        ("patch", f"{API}/projects/{pid}/tasks/{world['task']}", {"title": "tampered"}),
        ("post", f"{API}/projects/{pid}/decisions", {"title": "x", "statement": "y"}),
        ("patch", f"{API}/projects/{pid}/decisions/{world['decision']}", {"statement": "tampered"}),
        ("post", f"{API}/projects/{pid}/members", {"email": "outsider@example.com", "role": "owner"}),
        ("post", f"{API}/projects/{pid}/channels", {"name": "intrusion"}),
    ]
    for method, path, body in writes:
        kwargs = {"headers": h}
        if body is not None:
            kwargs["json"] = body
        response = getattr(o, method)(path, **kwargs)
        assert response.status_code == 404, f"{method} {path} gave {response.status_code}"


def test_non_member_cannot_open_project_websocket(world, app) -> None:  # type: ignore[no-untyped-def]
    from starlette.websockets import WebSocketDisconnect

    o, pid = world["outsider"], world["pid"]
    token = o.cookies["ns_session"]
    with (
        pytest.raises(WebSocketDisconnect),
        TestClient(app).websocket_connect(
            f"/api/v1/ws/projects/{pid}", headers={"origin": "http://localhost:5173", "cookie": f"ns_session={token}"}
        ) as ws,
    ):
        ws.receive_json()


def test_unknown_project_is_indistinguishable_from_forbidden(world) -> None:  # type: ignore[no-untyped-def]
    o = world["outsider"]
    real = o.get(f"{API}/projects/{world['pid']}")
    fake = o.get(f"{API}/projects/{uuid.uuid4()}")
    assert real.status_code == fake.status_code == 404
    assert real.json() == fake.json()


def test_outsider_project_list_is_empty(world) -> None:  # type: ignore[no-untyped-def]
    assert world["outsider"].get(f"{API}/projects").json() == []


def test_viewer_can_read_but_not_write(world, client_factory) -> None:  # type: ignore[no-untyped-def]
    owner, pid = world["owner"], world["pid"]
    viewer = client_factory()
    register(viewer, "viewer@example.com", "Viewer")
    add_member(owner, pid, "viewer@example.com", role="viewer")
    assert viewer.get(f"{API}/projects/{pid}/tasks").status_code == 200
    denied = viewer.post(
        f"{API}/projects/{pid}/channels/{world['channel']}/messages",
        json={"body": "hello"},
        headers=csrf_headers(viewer),
    )
    assert denied.status_code == 403
    assert (
        viewer.post(f"{API}/projects/{pid}/tasks", json={"title": "x"}, headers=csrf_headers(viewer)).status_code == 403
    )


def test_member_cannot_manage_members(world, client_factory) -> None:  # type: ignore[no-untyped-def]
    owner, pid = world["owner"], world["pid"]
    member = client_factory()
    register(member, "member@example.com", "Member")
    add_member(owner, pid, "member@example.com", role="member")
    response = member.post(
        f"{API}/projects/{pid}/members",
        json={"email": "outsider@example.com", "role": "viewer"},
        headers=csrf_headers(member),
    )
    assert response.status_code == 403


def test_admin_cannot_grant_or_touch_owner(world, client_factory) -> None:  # type: ignore[no-untyped-def]
    owner, pid = world["owner"], world["pid"]
    admin = client_factory()
    admin_user = register(admin, "admin@example.com", "Admin")
    add_member(owner, pid, "admin@example.com", role="admin")

    grant = admin.post(
        f"{API}/projects/{pid}/members",
        json={"email": "outsider@example.com", "role": "owner"},
        headers=csrf_headers(admin),
    )
    assert grant.status_code == 403

    members = owner.get(f"{API}/projects/{pid}/members").json()
    owner_id = next(m["user_id"] for m in members if m["role"] == "owner")
    demote_owner = admin.patch(
        f"{API}/projects/{pid}/members/{owner_id}", json={"role": "member"}, headers=csrf_headers(admin)
    )
    assert demote_owner.status_code == 403

    # Admin may still manage ordinary members, but cannot promote itself above admin.
    self_promote = admin.patch(
        f"{API}/projects/{pid}/members/{admin_user['id']}", json={"role": "owner"}, headers=csrf_headers(admin)
    )
    assert self_promote.status_code == 403


def test_last_owner_cannot_be_removed_or_demoted(world) -> None:  # type: ignore[no-untyped-def]
    owner, pid = world["owner"], world["pid"]
    members = owner.get(f"{API}/projects/{pid}/members").json()
    owner_id = members[0]["user_id"]
    assert (
        owner.patch(
            f"{API}/projects/{pid}/members/{owner_id}", json={"role": "admin"}, headers=csrf_headers(owner)
        ).status_code
        == 409
    )
    assert owner.delete(f"{API}/projects/{pid}/members/{owner_id}", headers=csrf_headers(owner)).status_code == 409


def test_removed_member_loses_access_immediately(world, client_factory) -> None:  # type: ignore[no-untyped-def]
    owner, pid = world["owner"], world["pid"]
    member = client_factory()
    member_user = register(member, "former@example.com", "Former")
    add_member(owner, pid, "former@example.com", role="member")
    assert member.get(f"{API}/projects/{pid}/tasks").status_code == 200
    removed = owner.delete(f"{API}/projects/{pid}/members/{member_user['id']}", headers=csrf_headers(owner))
    assert removed.status_code == 204
    assert member.get(f"{API}/projects/{pid}/tasks").status_code == 404
