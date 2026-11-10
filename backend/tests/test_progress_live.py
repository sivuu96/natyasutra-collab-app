"""A runtime progress event reaches a connected project socket as a live frame."""

import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from helpers import API
from test_run_lease import _exchange, _start_run

ORIGIN = "http://localhost:5173"


def test_progress_from_the_runtime_is_delivered_to_the_socket(fake_app: FastAPI, fake_client: TestClient) -> None:
    run = _start_run(fake_app, fake_client, handle=f"live{uuid.uuid4().hex[:6]}")
    body = _exchange(fake_client, run["token"], run["execution_name"]).json()
    invocation = body["invocation_id"]
    with fake_app.state.session_factory() as db:
        from app.models.bridge import AgentInvocation

        project_id = str(db.get(AgentInvocation, uuid.UUID(invocation)).project_id)
    headers = {"origin": ORIGIN, "cookie": f"ns_session={fake_client.cookies['ns_session']}"}
    with fake_client.websocket_connect(f"/api/v1/ws/projects/{project_id}?since=0", headers=headers) as ws:
        response = fake_client.post(
            f"{API}/internal/executions/events",
            json={"sequence": 0, "type": "progress", "text": "Reading the repository"},
            headers={"Authorization": f"Bearer {body['runtime_token']}"},
        )
        assert response.status_code == 200, response.text
        frame = None
        for _ in range(10):
            candidate = ws.receive_json()
            if candidate.get("event_type") == "agent.invocation.progress":
                frame = candidate
                break
    assert frame is not None
    assert frame["payload"]["stage"] == "Reading the repository"
