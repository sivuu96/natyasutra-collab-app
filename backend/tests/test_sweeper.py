"""Stuck-run sweeper, cloud cancel, and progress events."""

import datetime as dt
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from helpers import API, csrf_headers
from sqlalchemy import update
from test_run_lease import _exchange, _start_run

from app.db.base import utcnow
from app.models.agents import AgentInvocation
from app.models.enums import InvocationStatus
from app.models.execution import ExecutionOutbox
from app.services.execution import FakeExecutionProvider
from app.services.runtime_events import RunEventIn, apply_runtime_event
from app.services.sweeper import START_TIMEOUT, stuck_reason, sweep_runs


def _invocation(app: FastAPI, invocation_id: uuid.UUID) -> AgentInvocation:
    with app.state.session_factory() as db:
        invocation = db.get(AgentInvocation, invocation_id)
        assert invocation is not None
        db.expunge(invocation)
        return invocation


def test_stuck_reasons_follow_the_limits() -> None:
    now = utcnow()
    queued = AgentInvocation(
        status=InvocationStatus.QUEUED.value, created_at=now - START_TIMEOUT - dt.timedelta(minutes=1)
    )
    assert stuck_reason(queued, now) == "start_timeout"
    running = AgentInvocation(
        status=InvocationStatus.RUNNING.value,
        created_at=now - dt.timedelta(minutes=10),
        last_activity_at=now - dt.timedelta(minutes=6),
    )
    assert stuck_reason(running, now) == "heartbeat_lost"
    fresh = AgentInvocation(
        status=InvocationStatus.RUNNING.value,
        created_at=now - dt.timedelta(minutes=10),
        last_activity_at=now - dt.timedelta(minutes=1),
    )
    assert stuck_reason(fresh, now) is None
    old = AgentInvocation(
        status=InvocationStatus.RUNNING.value, created_at=now - dt.timedelta(minutes=41), last_activity_at=now
    )
    assert stuck_reason(old, now) == "timed_out"


def test_a_queued_run_that_never_started_is_failed_and_never_dispatched(
    fake_app: FastAPI, fake_client: TestClient
) -> None:
    run = _start_run(fake_app, fake_client, handle=f"stuck{uuid.uuid4().hex[:6]}")
    with fake_app.state.session_factory() as db:
        db.execute(
            update(AgentInvocation)
            .where(AgentInvocation.id == run["invocation_id"])
            .values(
                status=InvocationStatus.QUEUED.value,
                created_at=utcnow() - dt.timedelta(minutes=20),
                last_activity_at=None,
            )
        )
        db.execute(
            update(ExecutionOutbox)
            .where(ExecutionOutbox.invocation_id == run["invocation_id"])
            .values(status="pending", provider_execution_id=None)
        )
        db.commit()

    provider = FakeExecutionProvider()
    sweep_runs(fake_app.state.session_factory, provider)

    invocation = _invocation(fake_app, run["invocation_id"])
    assert invocation.status == InvocationStatus.FAILED.value
    assert invocation.error_code == "start_timeout"
    assert provider.submitted == [] and provider.cancelled == []


def test_a_silent_running_run_is_failed_and_its_execution_is_stopped_once(
    fake_app: FastAPI, fake_client: TestClient
) -> None:
    run = _start_run(fake_app, fake_client, handle=f"silent{uuid.uuid4().hex[:6]}")
    body = _exchange(fake_client, run["token"], run["execution_name"]).json()
    assert body["invocation_id"]
    with fake_app.state.session_factory() as db:
        db.execute(
            update(AgentInvocation)
            .where(AgentInvocation.id == run["invocation_id"])
            .values(status=InvocationStatus.RUNNING.value, last_activity_at=utcnow() - dt.timedelta(minutes=30))
        )
        db.commit()

    provider = FakeExecutionProvider()
    sweep_runs(fake_app.state.session_factory, provider)
    sweep_runs(fake_app.state.session_factory, provider)  # second pass must not stop it again

    invocation = _invocation(fake_app, run["invocation_id"])
    assert invocation.error_code == "heartbeat_lost"
    assert provider.cancelled == [run["execution_name"]]


def test_cancelling_a_cloud_run_marks_it_and_the_worker_stops_its_execution(
    fake_app: FastAPI, fake_client: TestClient
) -> None:
    run = _start_run(fake_app, fake_client, handle=f"cancel{uuid.uuid4().hex[:6]}")
    invocation = _invocation(fake_app, run["invocation_id"])
    response = fake_client.post(
        f"{API}/projects/{invocation.project_id}/invocations/{run['invocation_id']}/cancel",
        headers=csrf_headers(fake_client),
    )
    assert response.status_code == 200, response.text
    assert _invocation(fake_app, run["invocation_id"]).status == InvocationStatus.CANCELLED.value

    provider = FakeExecutionProvider()
    sweep_runs(fake_app.state.session_factory, provider)
    assert provider.cancelled == [run["execution_name"]]


def test_progress_is_published_and_counts_as_activity(fake_app: FastAPI, fake_client: TestClient) -> None:
    run = _start_run(fake_app, fake_client, handle=f"prog{uuid.uuid4().hex[:6]}")
    _exchange(fake_client, run["token"], run["execution_name"])
    with fake_app.state.session_factory() as db:
        _, publish = apply_runtime_event(
            db, run["invocation_id"], RunEventIn(sequence=0, type="progress", text="Reading the repository")
        )
        db.commit()
    event_types = [event["event_type"] for _, event in publish]
    assert event_types == ["agent.invocation.progress"]
    assert publish[0][1]["payload"]["stage"] == "Reading the repository"
    assert _invocation(fake_app, run["invocation_id"]).last_activity_at is not None


def test_a_heartbeat_keeps_a_run_alive(fake_app: FastAPI, fake_client: TestClient) -> None:
    run = _start_run(fake_app, fake_client, handle=f"beat{uuid.uuid4().hex[:6]}")
    _exchange(fake_client, run["token"], run["execution_name"])
    with fake_app.state.session_factory() as db:
        db.execute(
            update(AgentInvocation)
            .where(AgentInvocation.id == run["invocation_id"])
            .values(status=InvocationStatus.RUNNING.value, last_activity_at=utcnow() - dt.timedelta(minutes=4))
        )
        db.commit()
    with fake_app.state.session_factory() as db:
        apply_runtime_event(db, run["invocation_id"], RunEventIn(sequence=1, type="heartbeat"))
        db.commit()
    provider = FakeExecutionProvider()
    sweep_runs(fake_app.state.session_factory, provider)
    assert _invocation(fake_app, run["invocation_id"]).status == InvocationStatus.RUNNING.value
