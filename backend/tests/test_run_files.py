"""Files an agent returns in its reply: stored with the run and listed with it, readable by people who see the run."""

import uuid
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from helpers import API, csrf_headers, register
from test_run_events import _post, _runtime_token
from test_run_lease import _exchange, _start_run
from test_session_transcript import _session_with_agent, _turn

from app.services.execution import FakeExecutionProvider, process_once


def _files_of(client: TestClient, project_id: str, invocation_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = client.get(f"{API}/projects/{project_id}/invocations").json()
    return next(row["files"] for row in rows if row["id"] == str(invocation_id))


def test_files_in_a_reply_are_stored_listed_and_downloadable_as_attachments(
    fake_app: FastAPI, fake_client: TestClient
) -> None:
    invocation_id, token = _runtime_token(fake_app, fake_client)
    _post(fake_client, token, {"sequence": 0, "type": "started"})
    body = "# Plan\n\n- first step\n"
    assert (
        _post(fake_client, token, {"sequence": 1, "type": "file", "name": "plan.md", "content": body}).status_code
        == 200
    )
    _post(fake_client, token, {"sequence": 2, "type": "message", "text": "Saved the plan."})
    assert _post(fake_client, token, {"sequence": 3, "type": "completed"}).status_code == 200

    project_id = str(_invocation_project(fake_app, invocation_id))
    files = _files_of(fake_client, project_id, invocation_id)
    assert [(f["name"], f["size_bytes"]) for f in files] == [("plan.md", len(body.encode()))]

    response = fake_client.get(f"{API}/projects/{project_id}/invocations/{invocation_id}/files/{files[0]['id']}")
    assert response.status_code == 200
    assert response.text == body
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["content-disposition"] == 'attachment; filename="plan.md"'
    assert response.headers["x-content-type-options"] == "nosniff"


def _invocation_project(app: FastAPI, invocation_id: uuid.UUID) -> uuid.UUID:
    from app.models.agents import AgentInvocation

    with app.state.session_factory() as db:
        return db.get(AgentInvocation, invocation_id).project_id  # type: ignore[union-attr]


def test_names_and_sizes_are_checked_by_the_hub(fake_app: FastAPI, fake_client: TestClient) -> None:
    _, token = _runtime_token(fake_app, fake_client)
    _post(fake_client, token, {"sequence": 0, "type": "started"})
    for sequence, name in enumerate(["../escape.md", "a/b.md", ".hidden", "with space.md"], start=1):
        response = _post(fake_client, token, {"sequence": sequence, "type": "file", "name": name, "content": "x"})
        assert response.status_code == 422, name
    too_big = "x" * 100_001
    assert (
        _post(fake_client, token, {"sequence": 9, "type": "file", "name": "big.txt", "content": too_big}).status_code
        == 422
    )


def test_at_most_five_files_per_run_and_no_repeated_names(fake_app: FastAPI, fake_client: TestClient) -> None:
    run = _start_run(fake_app, fake_client, handle=f"limit{uuid.uuid4().hex[:6]}")
    token = _exchange(fake_client, run["token"], run["execution_name"]).json()["runtime_token"]
    _post(fake_client, token, {"sequence": 0, "type": "started"})
    sequence = 1
    for n in range(7):
        _post(fake_client, token, {"sequence": sequence, "type": "file", "name": f"part{n}.txt", "content": "x"})
        sequence += 1
    _post(fake_client, token, {"sequence": sequence, "type": "file", "name": "part0.txt", "content": "again"})
    stored = _files_of(fake_client, str(_invocation_project(fake_app, run["invocation_id"])), run["invocation_id"])
    assert len(stored) == 5
    assert len({f["name"] for f in stored}) == 5


def test_a_finished_run_cannot_add_files(fake_app: FastAPI, fake_client: TestClient) -> None:
    _, token = _runtime_token(fake_app, fake_client)
    _post(fake_client, token, {"sequence": 0, "type": "started"})
    _post(fake_client, token, {"sequence": 1, "type": "completed"})
    response = _post(fake_client, token, {"sequence": 2, "type": "file", "name": "late.txt", "content": "x"})
    assert response.status_code == 409
    assert response.json()["detail"] == "run_finished"


def test_a_non_member_gets_not_found_for_a_run_file(
    fake_app: FastAPI, fake_client: TestClient, client_factory: Any
) -> None:
    _, token = _runtime_token(fake_app, fake_client)
    _post(fake_client, token, {"sequence": 0, "type": "started"})
    _post(fake_client, token, {"sequence": 1, "type": "file", "name": "secret.txt", "content": "private"})
    project_id = str(_invocation_project(fake_app, _last_invocation(fake_app)))
    invocation_id = _last_invocation(fake_app)
    file_id = _files_of(fake_client, project_id, invocation_id)[0]["id"]

    outsider = client_factory()
    register(outsider, "files-outsider@example.com", "Outsider")
    response = outsider.get(f"{API}/projects/{project_id}/invocations/{invocation_id}/files/{file_id}")
    assert response.status_code == 404
    assert "private" not in response.text


def _last_invocation(app: FastAPI) -> uuid.UUID:
    from sqlalchemy import select

    from app.models.agents import AgentInvocation

    with app.state.session_factory() as db:
        return db.scalars(select(AgentInvocation.id).order_by(AgentInvocation.created_at.desc())).first()  # type: ignore[return-value]


def test_a_private_session_reply_is_readable_only_by_its_owner(
    fake_app: FastAPI, fake_client: TestClient, client_factory: Any
) -> None:
    project_id, session_id = _session_with_agent(fake_app, fake_client, "fileprivate")
    _turn(fake_client, project_id, session_id, "write me a note")
    provider = fake_app.state.execution_provider
    assert isinstance(provider, FakeExecutionProvider)
    process_once(fake_app.state.session_factory, provider, owner="test-worker")
    _, _, execution_name = provider.submitted[-1]
    invocation_id = provider.submitted[-1][0]
    token_body = _exchange(fake_client, provider.lease_tokens[execution_name], execution_name).json()
    _post(fake_client, token_body["runtime_token"], {"sequence": 0, "type": "started"})
    _post(fake_client, token_body["runtime_token"], {"sequence": 1, "type": "file", "name": "note.md", "content": "hi"})
    _post(fake_client, token_body["runtime_token"], {"sequence": 2, "type": "message", "text": "Done."})
    file_id = _files_of(fake_client, project_id, invocation_id)[0]["id"]

    assert (
        fake_client.get(f"{API}/projects/{project_id}/invocations/{invocation_id}/files/{file_id}").status_code == 200
    )

    teammate = client_factory()
    register(teammate, "files-teammate@example.com", "Teammate")
    fake_client.post(
        f"{API}/projects/{project_id}/members",
        json={"email": "files-teammate@example.com", "role": "member"},
        headers=csrf_headers(fake_client),
    )
    assert teammate.get(f"{API}/projects/{project_id}/invocations/{invocation_id}/files/{file_id}").status_code == 404
