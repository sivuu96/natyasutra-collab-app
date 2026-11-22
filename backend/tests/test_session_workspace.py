"""A session turn's working folder: stored from the runtime's summary, shown to people who can read the session."""

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from helpers import API, register
from test_run_events import _post, _runtime_token
from test_run_lease import _exchange
from test_session_transcript import _session_with_agent, _turn

from app.services.execution import FakeExecutionProvider, process_once

SUMMARY: dict[str, Any] = {
    "file_count": 2,
    "total_bytes": 30,
    "files": [{"path": "notes.md", "size": 10}, {"path": "src/main.py", "size": 20}],
    "changes": [{"path": "notes.md", "change": "changed"}, {"path": "src/main.py", "change": "added"}],
    "diff": "+ a new line\n",
    "diff_truncated": False,
}


def _session_turn_token(app: FastAPI, client: TestClient) -> tuple[str, str, str]:
    """Start a turn of a session. Returns (project_id, session_id, runtime token) for that turn."""
    project_id, session_id = _session_with_agent(app, client, "workspace")
    _turn(client, project_id, session_id, "change the notes")
    provider = app.state.execution_provider
    assert isinstance(provider, FakeExecutionProvider)
    process_once(app.state.session_factory, provider, owner="test-worker")
    _, _, execution_name = provider.submitted[-1]
    body = _exchange(client, provider.lease_tokens[execution_name], execution_name).json()
    return project_id, session_id, body["runtime_token"]


def _workspace(client: TestClient, project_id: str, session_id: str) -> Any:
    return client.get(f"{API}/projects/{project_id}/sessions/{session_id}/workspace")


def test_a_session_turns_summary_is_stored_and_shown(fake_app: FastAPI, fake_client: TestClient) -> None:
    project_id, session_id, token = _session_turn_token(fake_app, fake_client)
    _post(fake_client, token, {"sequence": 0, "type": "started"})
    stored = _post(fake_client, token, {"sequence": 1, "type": "workspace", **SUMMARY})
    assert stored.status_code == 200
    _post(fake_client, token, {"sequence": 2, "type": "message", "text": "Done."})
    _post(fake_client, token, {"sequence": 3, "type": "completed"})

    body = _workspace(fake_client, project_id, session_id).json()

    assert body["file_count"] == 2 and body["total_bytes"] == 30
    assert body["files"] == SUMMARY["files"]
    assert body["changes"] == SUMMARY["changes"]
    assert body["diff"] == "+ a new line\n"
    assert body["diff_truncated"] is False
    assert body["invocation_id"] is not None


def test_a_session_with_no_saved_turn_has_no_workspace(fake_app: FastAPI, fake_client: TestClient) -> None:
    project_id, session_id = _session_with_agent(fake_app, fake_client, "workspaceempty")
    assert _workspace(fake_client, project_id, session_id).json() is None


def test_a_malformed_change_is_refused(fake_app: FastAPI, fake_client: TestClient) -> None:
    _, _, token = _session_turn_token(fake_app, fake_client)
    _post(fake_client, token, {"sequence": 0, "type": "started"})
    bad = {**SUMMARY, "changes": [{"path": "notes.md", "change": "renamed"}]}

    response = _post(fake_client, token, {"sequence": 1, "type": "workspace", **bad})

    assert response.status_code == 422


def test_a_channel_run_has_no_workspace_to_keep(fake_app: FastAPI, fake_client: TestClient) -> None:
    _, token = _runtime_token(fake_app, fake_client)
    _post(fake_client, token, {"sequence": 0, "type": "started"})

    response = _post(fake_client, token, {"sequence": 1, "type": "workspace", **SUMMARY})

    assert response.status_code == 200  # accepted, and nothing is kept for a channel run


def test_someone_who_cannot_read_a_private_session_cannot_see_its_workspace(
    fake_app: FastAPI, fake_client: TestClient, client_factory: Any
) -> None:
    project_id, session_id, token = _session_turn_token(fake_app, fake_client)
    _post(fake_client, token, {"sequence": 0, "type": "started"})
    _post(fake_client, token, {"sequence": 1, "type": "workspace", **SUMMARY})

    outsider = client_factory()
    register(outsider, "workspace-outsider@example.com", "Outsider")

    response = _workspace(outsider, project_id, session_id)
    assert response.status_code == 404
    assert "notes.md" not in response.text
