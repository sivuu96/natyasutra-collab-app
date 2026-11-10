"""Runtime events: a run's progress is recorded once per sequence number, replies are stored, and a run ends cleanly."""

import uuid
from typing import Any

import jwt
from fastapi import FastAPI
from fastapi.testclient import TestClient
from helpers import API
from sqlalchemy import select
from test_run_lease import _exchange, _start_run

from app.models.agents import AgentInvocation
from app.models.enums import InvocationStatus
from app.models.execution import RunEvent
from app.models.work import Message

EVENTS = f"{API}/internal/executions/events"


def _post(client: TestClient, token: str, event: dict[str, Any]) -> Any:
    return client.post(EVENTS, json=event, headers={"Authorization": f"Bearer {token}"})


def _runtime_token(app: FastAPI, client: TestClient) -> tuple[uuid.UUID, str]:
    run = _start_run(app, client, handle=f"events{uuid.uuid4().hex[:6]}")
    body = _exchange(client, run["token"], run["execution_name"]).json()
    return run["invocation_id"], body["runtime_token"]


def test_a_full_run_is_recorded_and_its_reply_is_saved_as_a_channel_message(
    fake_app: FastAPI, fake_client: TestClient
) -> None:
    invocation_id, token = _runtime_token(fake_app, fake_client)

    assert _post(fake_client, token, {"sequence": 0, "type": "started"}).json() == {"duplicate": False}
    assert _post(fake_client, token, {"sequence": 1, "type": "output", "text": "Looking at it"}).status_code == 200
    assert (
        _post(fake_client, token, {"sequence": 2, "type": "message", "text": "Here is the summary."}).status_code == 200
    )
    done = _post(fake_client, token, {"sequence": 3, "type": "completed"})
    assert done.status_code == 200

    with fake_app.state.session_factory() as db:
        invocation = db.get(AgentInvocation, invocation_id)
        assert invocation is not None
        assert invocation.status == InvocationStatus.COMPLETED.value
        assert invocation.completed_at is not None
        message = db.get(Message, invocation.result_message_id)
        assert message is not None
        assert message.body == "Here is the summary."
        assert message.author_agent_id == invocation.agent_id
        assert message.channel_id == invocation.channel_id
        events = db.scalars(select(RunEvent).where(RunEvent.invocation_id == invocation_id)).all()
        assert sorted(e.sequence for e in events) == [0, 1, 2, 3]


def test_a_repeated_sequence_is_a_no_op(fake_app: FastAPI, fake_client: TestClient) -> None:
    invocation_id, token = _runtime_token(fake_app, fake_client)
    _post(fake_client, token, {"sequence": 0, "type": "started"})
    first = _post(fake_client, token, {"sequence": 1, "type": "message", "text": "once"})
    retry = _post(fake_client, token, {"sequence": 1, "type": "message", "text": "once"})

    assert first.json() == {"duplicate": False}
    assert retry.json() == {"duplicate": True}
    with fake_app.state.session_factory() as db:
        messages = db.scalars(select(Message).where(Message.body == "once")).all()
        assert len(messages) == 1


def test_events_after_completion_are_refused(fake_app: FastAPI, fake_client: TestClient) -> None:
    _, token = _runtime_token(fake_app, fake_client)
    _post(fake_client, token, {"sequence": 0, "type": "completed"})

    response = _post(fake_client, token, {"sequence": 1, "type": "message", "text": "late"})

    assert response.status_code == 409
    assert response.json()["detail"] == "run_finished"


def test_a_failed_run_records_its_error_code(fake_app: FastAPI, fake_client: TestClient) -> None:
    invocation_id, token = _runtime_token(fake_app, fake_client)
    _post(fake_client, token, {"sequence": 0, "type": "failed", "code": "timeout", "message": "too slow"})

    with fake_app.state.session_factory() as db:
        invocation = db.get(AgentInvocation, invocation_id)
        assert invocation is not None
        assert invocation.status == InvocationStatus.FAILED.value
        assert invocation.error_code == "timeout"
        assert invocation.error_message == "too slow"


def test_a_missing_or_forged_runtime_token_is_refused(fake_app: FastAPI, fake_client: TestClient) -> None:
    _, token = _runtime_token(fake_app, fake_client)
    event = {"sequence": 0, "type": "started"}

    assert fake_client.post(EVENTS, json=event).status_code == 401
    assert _post(fake_client, "not-a-token", event).status_code == 401

    forged = jwt.encode(
        {"sub": str(uuid.uuid4()), "aud": "collab-runtime", "iss": "collab-hub", "exp": 4102444800},
        "wrong-secret-value-that-is-long-enough-123456",
        algorithm="HS256",
    )
    assert _post(fake_client, forged, event).status_code == 401


def test_a_lease_token_cannot_be_used_as_a_runtime_token(fake_app: FastAPI, fake_client: TestClient) -> None:
    run = _start_run(fake_app, fake_client, handle=f"lease{uuid.uuid4().hex[:6]}")
    response = _post(fake_client, run["token"], {"sequence": 0, "type": "started"})
    assert response.status_code == 401
