"""Run lease: one-use exchange, execution binding, expiry, revocation, and what the job may see."""

import datetime as dt
import uuid
from typing import Any

import jwt
from fastapi import FastAPI
from fastapi.testclient import TestClient
from helpers import API, create_project, csrf_headers, general_channel, register
from sqlalchemy import select, update

from app.db.base import utcnow
from app.models.bridge import Agent, AgentInvocation, ProjectAgent
from app.models.enums import InvocationStatus
from app.models.execution import ExecutionOutbox, InvocationLease
from app.services.execution import FakeExecutionProvider, hash_token, process_once
from app.services.leases import RUNTIME_AUDIENCE

EXCHANGE = f"{API}/internal/executions/exchange"


def _start_run(app: FastAPI, client: TestClient, handle: str = "leaseclaude") -> dict[str, Any]:
    """Queue a cloud run through the API and let the worker start it. Returns the ids and the lease token."""
    owner = register(client, f"{handle}-owner@example.com", "Owner")
    project = create_project(client, f"{handle} project")
    project_id = project["id"]
    channel_id = general_channel(client, project_id)
    owner_id = uuid.UUID(owner["id"])
    with app.state.session_factory() as db:
        agent = Agent(
            owner_user_id=owner_id,
            device_id=None,
            handle=handle,
            display_name="Lease test agent",
            harness="claude_code",
            capabilities=[],
        )
        db.add(agent)
        db.flush()
        db.add(ProjectAgent(project_id=uuid.UUID(project_id), agent_id=agent.id, added_by_user_id=owner_id))
        db.commit()
    response = client.post(
        f"{API}/projects/{project_id}/channels/{channel_id}/messages",
        json={"body": f"@{handle} summarise the open decisions"},
        headers=csrf_headers(client),
    )
    assert response.status_code == 201, response.text

    provider = app.state.execution_provider
    assert isinstance(provider, FakeExecutionProvider)
    process_once(app.state.session_factory, provider, owner="test-worker")
    invocation_id, _, execution_name = provider.submitted[-1]
    return {
        "invocation_id": invocation_id,
        "execution_name": execution_name,
        "token": provider.lease_tokens[execution_name],
        "provider": provider,
    }


def _exchange(client: TestClient, token: str, execution_name: str) -> Any:
    return client.post(
        EXCHANGE,
        json={"execution_name": execution_name},
        headers={"Authorization": f"Bearer {token}"},
    )


def test_exchange_returns_the_run_context_and_a_runtime_token(fake_app: FastAPI, fake_client: TestClient) -> None:
    run = _start_run(fake_app, fake_client)

    response = _exchange(fake_client, run["token"], run["execution_name"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["invocation_id"] == str(run["invocation_id"])
    assert body["prompt"] == "@leaseclaude summarise the open decisions"
    assert body["agent_handle"] == "leaseclaude"
    assert body["harness"] == "claude_code"
    assert body["runtime_expires_in"] == 15 * 60
    with fake_app.state.session_factory() as db:
        invocation = db.get(AgentInvocation, run["invocation_id"])
        assert invocation is not None
        assert invocation.status == InvocationStatus.ACCEPTED.value


def test_the_lease_is_single_use(fake_app: FastAPI, fake_client: TestClient) -> None:
    run = _start_run(fake_app, fake_client)
    assert _exchange(fake_client, run["token"], run["execution_name"]).status_code == 200

    second = _exchange(fake_client, run["token"], run["execution_name"])

    assert second.status_code == 409
    assert second.json()["detail"] == "lease_used"


def test_a_mismatched_execution_is_refused_without_burning_the_lease(
    fake_app: FastAPI, fake_client: TestClient
) -> None:
    run = _start_run(fake_app, fake_client)

    wrong = _exchange(fake_client, run["token"], "projects/x/locations/us-west1/jobs/j/executions/other")
    assert wrong.status_code == 409
    assert wrong.json()["detail"] == "execution_mismatch"

    right = _exchange(fake_client, run["token"], run["execution_name"])
    assert right.status_code == 200


def test_an_expired_lease_is_refused(fake_app: FastAPI, fake_client: TestClient) -> None:
    run = _start_run(fake_app, fake_client)
    with fake_app.state.session_factory() as db:
        db.execute(
            update(InvocationLease)
            .where(InvocationLease.invocation_id == run["invocation_id"])
            .values(expires_at=utcnow() - dt.timedelta(seconds=1))
        )
        db.commit()

    response = _exchange(fake_client, run["token"], run["execution_name"])

    assert response.status_code == 401
    assert response.json()["detail"] == "lease_expired"


def test_a_retry_revokes_the_earlier_lease(fake_app: FastAPI, fake_client: TestClient) -> None:
    # First attempt fails at the provider, so its lease is never bound and must not be usable later.
    run = _start_run(fake_app, fake_client, handle="retryclaude")
    provider = run["provider"]
    first_token = run["token"]
    # Put the outbox row back to pending, as if the first start had failed before it was recorded.
    with fake_app.state.session_factory() as db:
        db.execute(
            update(ExecutionOutbox)
            .where(ExecutionOutbox.invocation_id == run["invocation_id"])
            .values(status="pending", attempts=0, next_attempt_at=utcnow(), lease_owner=None, lease_expires_at=None)
        )
        db.commit()
    provider.fail_next = 1
    later = utcnow() + dt.timedelta(hours=1)
    process_once(fake_app.state.session_factory, provider, owner="test-worker", now=later)  # attempt fails
    process_once(
        fake_app.state.session_factory, provider, owner="test-worker", now=later + dt.timedelta(hours=1)
    )  # retry succeeds
    new_execution = provider.submitted[-1][2]
    new_token = provider.lease_tokens[new_execution]
    assert new_token != first_token

    old = _exchange(fake_client, first_token, new_execution)
    assert old.status_code == 401
    assert old.json()["detail"] == "lease_revoked"

    assert _exchange(fake_client, new_token, new_execution).status_code == 200


def test_an_unknown_token_is_refused(fake_app: FastAPI, fake_client: TestClient) -> None:
    run = _start_run(fake_app, fake_client, handle="unknownclaude")
    response = _exchange(fake_client, "not-a-real-token", run["execution_name"])
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid_lease"


def test_a_missing_bearer_header_is_refused(fake_app: FastAPI, fake_client: TestClient) -> None:
    response = fake_client.post(EXCHANGE, json={"execution_name": "x"})
    assert response.status_code == 401


def test_a_cancelled_run_cannot_be_exchanged(fake_app: FastAPI, fake_client: TestClient) -> None:
    run = _start_run(fake_app, fake_client, handle="cancelclaude")
    with fake_app.state.session_factory() as db:
        invocation = db.get(AgentInvocation, run["invocation_id"])
        assert invocation is not None
        invocation.status = InvocationStatus.CANCELLED.value
        db.commit()

    response = _exchange(fake_client, run["token"], run["execution_name"])

    assert response.status_code == 409
    assert response.json()["detail"] == "run_not_active"


def test_only_the_hash_of_the_lease_is_stored(fake_app: FastAPI, fake_client: TestClient) -> None:
    run = _start_run(fake_app, fake_client, handle="hashclaude")
    with fake_app.state.session_factory() as db:
        lease = db.scalar(select(InvocationLease).where(InvocationLease.invocation_id == run["invocation_id"]))
        assert lease is not None
        assert lease.token_hash == hash_token(run["token"])
        assert run["token"] not in lease.token_hash


def test_the_runtime_token_is_scoped_to_its_run(fake_app: FastAPI, fake_client: TestClient) -> None:
    run = _start_run(fake_app, fake_client, handle="scopeclaude")
    body = _exchange(fake_client, run["token"], run["execution_name"]).json()

    claims = jwt.decode(
        body["runtime_token"],
        fake_app.state.settings.bridge_token_secret,
        algorithms=["HS256"],
        audience=RUNTIME_AUDIENCE,
    )

    assert claims["sub"] == str(run["invocation_id"])
