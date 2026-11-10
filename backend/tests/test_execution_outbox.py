"""Execution outbox: enqueue, leasing, retry/backoff, dead-lettering and provider idempotency."""

import datetime as dt
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import utcnow
from app.models.bridge import Agent, AgentInvocation, BridgeDevice
from app.models.enums import InvocationStatus
from app.models.execution import ExecutionOutbox
from app.models.identity import Team, User
from app.models.projects import Project
from app.services.execution import (
    FakeExecutionProvider,
    claim_due,
    enqueue_dispatch,
    process_once,
    retry_delay_seconds,
)


def _make_invocation(db: Session) -> uuid.UUID:
    suffix = uuid.uuid4().hex[:8]
    user = User(email=f"owner-{suffix}@example.com", display_name="Owner", password_hash=None)
    db.add(user)
    db.flush()
    team = Team(name="Team", slug=f"team-{suffix}")
    db.add(team)
    db.flush()
    project = Project(team_id=team.id, name="Project", slug=f"project-{suffix}")
    db.add(project)
    db.flush()
    device = BridgeDevice(owner_user_id=user.id, name="Laptop", secret_hash="x" * 64)
    db.add(device)
    db.flush()
    agent = Agent(
        owner_user_id=user.id,
        device_id=device.id,
        handle=f"agent{suffix}",
        display_name="Agent",
        harness="claude_code",
        capabilities=[],
    )
    db.add(agent)
    db.flush()
    invocation = AgentInvocation(
        project_id=project.id,
        agent_id=agent.id,
        requested_by_user_id=user.id,
        status=InvocationStatus.QUEUED.value,
        prompt="hello",
    )
    db.add(invocation)
    db.flush()
    return invocation.id


@pytest.fixture
def invocation_id(db: Session) -> uuid.UUID:
    invocation = _make_invocation(db)
    db.commit()
    return invocation


def _outbox_rows(db: Session) -> list[ExecutionOutbox]:
    db.expire_all()
    return list(db.scalars(select(ExecutionOutbox)).all())


def test_enqueue_is_idempotent_per_invocation(db: Session, invocation_id: uuid.UUID) -> None:
    first = enqueue_dispatch(db, invocation_id)
    second = enqueue_dispatch(db, invocation_id)
    db.commit()
    assert first.id == second.id
    assert len(_outbox_rows(db)) == 1


def test_successful_submit_marks_done_and_delivers_invocation(
    db: Session, session_factory: sessionmaker[Session], invocation_id: uuid.UUID
) -> None:
    enqueue_dispatch(db, invocation_id)
    db.commit()
    provider = FakeExecutionProvider()

    processed = process_once(session_factory, provider, owner="worker-a")

    assert processed == 1
    row = _outbox_rows(db)[0]
    assert row.status == "done"
    assert row.provider_execution_id is not None
    assert row.lease_owner is None
    assert len(provider.submitted) == 1
    assert provider.submitted[0][1] == f"dispatch:{invocation_id}"  # dedupe key sent as idempotency key
    invocation = db.get(AgentInvocation, invocation_id)
    assert invocation.status == InvocationStatus.DELIVERED.value
    assert invocation.delivered_at is not None


def test_failed_submit_backs_off_and_retries_later(
    db: Session, session_factory: sessionmaker[Session], invocation_id: uuid.UUID
) -> None:
    enqueue_dispatch(db, invocation_id)
    db.commit()
    provider = FakeExecutionProvider(fail_next=1)
    start = utcnow()

    process_once(session_factory, provider, owner="worker-a", now=start)

    row = _outbox_rows(db)[0]
    assert row.status == "pending"
    assert row.attempts == 1
    assert row.last_error and "simulated provider failure" in row.last_error
    # Not due again until the backoff has passed.
    assert process_once(session_factory, provider, owner="worker-a", now=start) == 0
    later = start + dt.timedelta(seconds=retry_delay_seconds(1) + 1)
    assert process_once(session_factory, provider, owner="worker-a", now=later) == 1
    assert _outbox_rows(db)[0].status == "done"


def test_repeated_failures_dead_letter_and_fail_the_invocation(
    db: Session, session_factory: sessionmaker[Session], invocation_id: uuid.UUID
) -> None:
    enqueue_dispatch(db, invocation_id)
    db.commit()
    provider = FakeExecutionProvider(fail_next=10)
    now = utcnow()
    for _ in range(3):
        process_once(session_factory, provider, owner="worker-a", now=now, max_attempts=3)
        now += dt.timedelta(hours=1)  # always past the backoff

    row = _outbox_rows(db)[0]
    assert row.status == "dead"
    assert row.attempts == 3
    invocation = db.get(AgentInvocation, invocation_id)
    assert invocation.status == InvocationStatus.FAILED.value
    assert invocation.error_code == "dispatch_failed"
    assert invocation.completed_at is not None


def test_live_lease_is_not_claimed_by_another_worker(
    db: Session, session_factory: sessionmaker[Session], invocation_id: uuid.UUID
) -> None:
    enqueue_dispatch(db, invocation_id)
    db.commit()
    now = utcnow()

    first = claim_due(db, owner="worker-a", now=now, lease_seconds=120)
    second = claim_due(db, owner="worker-b", now=now + dt.timedelta(seconds=30), lease_seconds=120)

    assert len(first) == 1
    assert second == []


def test_expired_lease_is_reclaimed(
    db: Session, session_factory: sessionmaker[Session], invocation_id: uuid.UUID
) -> None:
    enqueue_dispatch(db, invocation_id)
    db.commit()
    now = utcnow()
    assert len(claim_due(db, owner="worker-a", now=now, lease_seconds=60)) == 1

    reclaimed = claim_due(db, owner="worker-b", now=now + dt.timedelta(seconds=61), lease_seconds=60)

    assert len(reclaimed) == 1
    assert _outbox_rows(db)[0].lease_owner == "worker-b"


def test_fake_provider_is_idempotent_on_dedupe_key() -> None:
    provider = FakeExecutionProvider()
    invocation = uuid.uuid4()
    first = provider.submit(invocation_id=invocation, dedupe_key="dispatch:x", lease_token="t")
    second = provider.submit(invocation_id=invocation, dedupe_key="dispatch:x", lease_token="t")
    assert first == second
    assert len(provider.submitted) == 1


def test_backoff_is_capped() -> None:
    assert retry_delay_seconds(1) == 10
    assert retry_delay_seconds(2) == 20
    assert retry_delay_seconds(50) == 300
