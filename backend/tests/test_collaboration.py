"""Discussions, tasks, decisions, activity and search workflows."""

import pytest
from helpers import (
    API,
    add_member,
    create_project,
    csrf_headers,
    general_channel,
    register,
)


@pytest.fixture
def team(client_factory):  # type: ignore[no-untyped-def]
    owner = client_factory()
    register(owner, "lead@example.com", "Lead")
    project = create_project(owner, "NatyaSutra Port")
    pid = project["id"]
    member = client_factory()
    register(member, "dev@example.com", "Dev")
    add_member(owner, pid, "dev@example.com", role="member")
    return {"owner": owner, "member": member, "pid": pid, "channel": general_channel(owner, pid)}


# ---- discussions ------------------------------------------------------------------------------


def test_post_list_and_thread_reply(team) -> None:  # type: ignore[no-untyped-def]
    m, pid, ch = team["member"], team["pid"], team["channel"]
    root = m.post(
        f"{API}/projects/{pid}/channels/{ch}/messages",
        json={"body": "Starting Mohiniyattam notes"},
        headers=csrf_headers(m),
    ).json()
    reply = m.post(
        f"{API}/projects/{pid}/channels/{ch}/messages",
        json={"body": "Agreed", "parent_message_id": root["id"]},
        headers=csrf_headers(m),
    ).json()
    assert reply["parent_message_id"] == root["id"]
    listed = team["owner"].get(f"{API}/projects/{pid}/channels/{ch}/messages").json()
    assert [msg["body"] for msg in listed] == ["Starting Mohiniyattam notes", "Agreed"]
    assert listed[0]["author_display_name"] == "Dev"


def test_reply_to_message_in_other_channel_rejected(team) -> None:  # type: ignore[no-untyped-def]
    o, pid, ch = team["owner"], team["pid"], team["channel"]
    other = o.post(f"{API}/projects/{pid}/channels", json={"name": "backend"}, headers=csrf_headers(o)).json()
    root = o.post(f"{API}/projects/{pid}/channels/{ch}/messages", json={"body": "root"}, headers=csrf_headers(o)).json()
    response = o.post(
        f"{API}/projects/{pid}/channels/{other['id']}/messages",
        json={"body": "cross", "parent_message_id": root["id"]},
        headers=csrf_headers(o),
    )
    assert response.status_code == 422


def test_only_author_can_edit_message(team) -> None:  # type: ignore[no-untyped-def]
    m, o, pid, ch = team["member"], team["owner"], team["pid"], team["channel"]
    msg = m.post(f"{API}/projects/{pid}/channels/{ch}/messages", json={"body": "draft"}, headers=csrf_headers(m)).json()
    assert (
        o.patch(
            f"{API}/projects/{pid}/messages/{msg['id']}", json={"body": "hijack"}, headers=csrf_headers(o)
        ).status_code
        == 404
    )
    edited = m.patch(f"{API}/projects/{pid}/messages/{msg['id']}", json={"body": "final"}, headers=csrf_headers(m))
    assert edited.status_code == 200
    assert edited.json()["body"] == "final"
    assert edited.json()["edited_at"] is not None


def test_delete_is_soft_and_admin_can_moderate(team) -> None:  # type: ignore[no-untyped-def]
    m, o, pid, ch = team["member"], team["owner"], team["pid"], team["channel"]
    msg = m.post(
        f"{API}/projects/{pid}/channels/{ch}/messages", json={"body": "remove me"}, headers=csrf_headers(m)
    ).json()
    deleted = o.delete(f"{API}/projects/{pid}/messages/{msg['id']}", headers=csrf_headers(o))
    assert deleted.status_code == 200
    assert deleted.json()["body"] == ""
    assert deleted.json()["deleted_at"] is not None
    listed = o.get(f"{API}/projects/{pid}/channels/{ch}/messages").json()
    assert listed[0]["body"] == ""


def test_member_cannot_delete_others_message(team, client_factory) -> None:  # type: ignore[no-untyped-def]
    m, o, pid, ch = team["member"], team["owner"], team["pid"], team["channel"]
    msg = o.post(
        f"{API}/projects/{pid}/channels/{ch}/messages", json={"body": "owner post"}, headers=csrf_headers(o)
    ).json()
    assert m.delete(f"{API}/projects/{pid}/messages/{msg['id']}", headers=csrf_headers(m)).status_code == 404


def test_duplicate_channel_name_conflicts(team) -> None:  # type: ignore[no-untyped-def]
    o, pid = team["owner"], team["pid"]
    assert (
        o.post(f"{API}/projects/{pid}/channels", json={"name": "product"}, headers=csrf_headers(o)).status_code == 201
    )
    assert (
        o.post(f"{API}/projects/{pid}/channels", json={"name": "product"}, headers=csrf_headers(o)).status_code == 409
    )


def test_channel_name_format_enforced(team) -> None:  # type: ignore[no-untyped-def]
    o, pid = team["owner"], team["pid"]
    response = o.post(f"{API}/projects/{pid}/channels", json={"name": "Bad Name!"}, headers=csrf_headers(o))
    assert response.status_code == 422


# ---- tasks ------------------------------------------------------------------------------------


def test_task_lifecycle_and_filters(team) -> None:  # type: ignore[no-untyped-def]
    m, o, pid = team["member"], team["owner"], team["pid"]
    members = o.get(f"{API}/projects/{pid}/members").json()
    dev_id = next(x["user_id"] for x in members if x["email"] == "dev@example.com")
    task = m.post(
        f"{API}/projects/{pid}/tasks",
        json={"title": "Port adapter", "priority": "high", "assignee_user_id": dev_id},
        headers=csrf_headers(m),
    ).json()
    assert task["status"] == "backlog"
    updated = m.patch(
        f"{API}/projects/{pid}/tasks/{task['id']}", json={"status": "in_progress"}, headers=csrf_headers(m)
    )
    assert updated.status_code == 200 and updated.json()["status"] == "in_progress"
    in_progress = o.get(f"{API}/projects/{pid}/tasks", params={"status": "in_progress"}).json()
    assert [t["id"] for t in in_progress] == [task["id"]]
    assert o.get(f"{API}/projects/{pid}/tasks", params={"assignee": dev_id}).json()[0]["id"] == task["id"]


def test_invalid_task_status_rejected(team) -> None:  # type: ignore[no-untyped-def]
    m, pid = team["member"], team["pid"]
    response = m.post(f"{API}/projects/{pid}/tasks", json={"title": "x", "status": "started"}, headers=csrf_headers(m))
    assert response.status_code == 422


def test_assignee_must_be_project_member(team) -> None:  # type: ignore[no-untyped-def]
    import uuid

    m, pid = team["member"], team["pid"]
    response = m.post(
        f"{API}/projects/{pid}/tasks",
        json={"title": "x", "assignee_user_id": str(uuid.uuid4())},
        headers=csrf_headers(m),
    )
    assert response.status_code == 422


# ---- decisions --------------------------------------------------------------------------------


def test_decision_starts_proposed_with_revision_history(team) -> None:  # type: ignore[no-untyped-def]
    m, o, pid = team["member"], team["owner"], team["pid"]
    decision = m.post(
        f"{API}/projects/{pid}/decisions",
        json={
            "title": "Use Codex app-server",
            "statement": "Adopt stdio app-server",
            "options": [{"name": "websocket"}, {"name": "stdio"}],
        },
        headers=csrf_headers(m),
    ).json()
    assert decision["status"] == "proposed"
    assert decision["current_revision"] == 1

    m.patch(
        f"{API}/projects/{pid}/decisions/{decision['id']}",
        json={"rationale": "stdio is default"},
        headers=csrf_headers(m),
    )
    revisions = o.get(f"{API}/projects/{pid}/decisions/{decision['id']}/revisions").json()
    assert [r["revision_no"] for r in revisions] == [1, 2]
    assert revisions[1]["snapshot"]["rationale"] == "stdio is default"


def test_only_decision_owner_or_admin_can_accept(team) -> None:  # type: ignore[no-untyped-def]
    m, o, pid = team["member"], team["owner"], team["pid"]
    decision = o.post(
        f"{API}/projects/{pid}/decisions", json={"title": "t", "statement": "s"}, headers=csrf_headers(o)
    ).json()
    attempt = m.patch(
        f"{API}/projects/{pid}/decisions/{decision['id']}", json={"status": "accepted"}, headers=csrf_headers(m)
    )
    assert attempt.status_code == 403
    accepted = o.patch(
        f"{API}/projects/{pid}/decisions/{decision['id']}", json={"status": "accepted"}, headers=csrf_headers(o)
    )
    assert accepted.status_code == 200 and accepted.json()["status"] == "accepted"


def test_decision_not_accepted_automatically_on_creation(team) -> None:  # type: ignore[no-untyped-def]
    m, pid = team["member"], team["pid"]
    decision = m.post(
        f"{API}/projects/{pid}/decisions",
        json={"title": "AI suggestion", "statement": "generated text", "status": "accepted"},
        headers=csrf_headers(m),
    ).json()
    assert decision["status"] == "proposed"


# ---- activity, audit and search -------------------------------------------------------------


def test_project_reports_latest_sequence_for_resume(team) -> None:  # type: ignore[no-untyped-def]
    m, o, pid, ch = team["member"], team["owner"], team["pid"], team["channel"]
    assert o.get(f"{API}/projects/{pid}").json()["latest_sequence"] == 0
    m.post(f"{API}/projects/{pid}/channels/{ch}/messages", json={"body": "one"}, headers=csrf_headers(m))
    assert o.get(f"{API}/projects/{pid}").json()["latest_sequence"] == 1


def test_activity_feed_is_sequenced_and_resumable(team) -> None:  # type: ignore[no-untyped-def]
    m, o, pid, ch = team["member"], team["owner"], team["pid"], team["channel"]
    for i in range(3):
        m.post(f"{API}/projects/{pid}/channels/{ch}/messages", json={"body": f"msg {i}"}, headers=csrf_headers(m))
    feed = o.get(f"{API}/projects/{pid}/activity").json()
    sequences = [e["sequence"] for e in feed]
    assert sequences == sorted(sequences)
    assert len(set(sequences)) == len(sequences)
    last = sequences[-1]
    m.post(f"{API}/projects/{pid}/channels/{ch}/messages", json={"body": "after"}, headers=csrf_headers(m))
    newer = o.get(f"{API}/projects/{pid}/activity", params={"after": last}).json()
    assert len(newer) == 1
    assert newer[0]["event_type"] == "conversation.message.created"
    assert newer[0]["sequence"] > last


def test_mentions_are_extracted_into_event_payload(team) -> None:  # type: ignore[no-untyped-def]
    m, o, pid, ch = team["member"], team["owner"], team["pid"], team["channel"]
    m.post(
        f"{API}/projects/{pid}/channels/{ch}/messages",
        json={"body": "@claude-architecture please review, cc @dev and @dev"},
        headers=csrf_headers(m),
    )
    event = [
        e for e in o.get(f"{API}/projects/{pid}/activity").json() if e["event_type"] == "conversation.message.created"
    ][-1]
    assert event["payload"]["mentions"] == ["claude-architecture", "dev"]


def test_audit_log_admin_only(team) -> None:  # type: ignore[no-untyped-def]
    m, o, pid = team["member"], team["owner"], team["pid"]
    assert m.get(f"{API}/projects/{pid}/audit").status_code == 403
    entries = o.get(f"{API}/projects/{pid}/audit").json()
    assert any(entry["action"] == "project.created" for entry in entries)


def test_audit_never_contains_message_bodies(team) -> None:  # type: ignore[no-untyped-def]
    m, o, pid, ch = team["member"], team["owner"], team["pid"], team["channel"]
    secret = "super-secret-phrase-xyz"
    m.post(f"{API}/projects/{pid}/channels/{ch}/messages", json={"body": secret}, headers=csrf_headers(m))
    assert secret not in str(o.get(f"{API}/projects/{pid}/audit").json())


def test_search_finds_messages_tasks_and_decisions_in_project_only(team, client_factory) -> None:  # type: ignore[no-untyped-def]
    m, o, pid, ch = team["member"], team["owner"], team["pid"], team["channel"]
    m.post(
        f"{API}/projects/{pid}/channels/{ch}/messages",
        json={"body": "Bharatanatyam adavus review"},
        headers=csrf_headers(m),
    )
    m.post(f"{API}/projects/{pid}/tasks", json={"title": "Bharatanatyam syllabus"}, headers=csrf_headers(m))
    m.post(
        f"{API}/projects/{pid}/decisions",
        json={"title": "Bharatanatyam pricing", "statement": "x"},
        headers=csrf_headers(m),
    )
    results = o.get(f"{API}/projects/{pid}/search", params={"q": "bharatanatyam"}).json()
    assert len(results["messages"]) == 1
    assert len(results["tasks"]) == 1
    assert len(results["decisions"]) == 1

    other = client_factory()
    register(other, "other@example.com")
    create_project(other, "Other Project")
    other_pid = other.get(f"{API}/projects").json()[0]["id"]
    assert other.get(f"{API}/projects/{pid}/search", params={"q": "bharatanatyam"}).status_code == 404
    assert other.get(f"{API}/projects/{other_pid}/search", params={"q": "bharatanatyam"}).json() == {
        "messages": [],
        "tasks": [],
        "decisions": [],
    }


def test_search_treats_wildcards_literally(team) -> None:  # type: ignore[no-untyped-def]
    m, o, pid, ch = team["member"], team["owner"], team["pid"], team["channel"]
    m.post(f"{API}/projects/{pid}/channels/{ch}/messages", json={"body": "100% done"}, headers=csrf_headers(m))
    m.post(f"{API}/projects/{pid}/channels/{ch}/messages", json={"body": "1000 lines"}, headers=csrf_headers(m))
    results = o.get(f"{API}/projects/{pid}/search", params={"q": "100%"}).json()
    assert [msg["snippet"] for msg in results["messages"]] == ["100% done"]


def test_deleted_messages_not_searchable(team) -> None:  # type: ignore[no-untyped-def]
    m, o, pid, ch = team["member"], team["owner"], team["pid"], team["channel"]
    msg = m.post(
        f"{API}/projects/{pid}/channels/{ch}/messages", json={"body": "ephemeral idea"}, headers=csrf_headers(m)
    ).json()
    m.delete(f"{API}/projects/{pid}/messages/{msg['id']}", headers=csrf_headers(m))
    assert o.get(f"{API}/projects/{pid}/search", params={"q": "ephemeral"}).json()["messages"] == []
