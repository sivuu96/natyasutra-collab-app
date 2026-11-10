"""Stuck-run sweeper for cloud agent runs.

Each pass does two things:
1. Fails cloud runs that have gone quiet, with a reason the UI can show:
   - start_timeout: never started (no exchange, no event) within START_TIMEOUT
   - heartbeat_lost: started, but no heartbeat or event within ACTIVITY_TIMEOUT
   - timed_out: still open MAX_RUN after creation
2. Asks the provider to stop the execution of any cancelled or failed run, exactly once. Cloud Run keeps
   the container alive until it is stopped, so this is what actually ends the work.

Bridge runs are not touched here; the bridge reports its own failures.
"""

import datetime as dt
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import utcnow
from app.models.bridge import Agent, AgentInvocation
from app.models.enums import TERMINAL_INVOCATION_STATUSES, InvocationStatus
from app.models.execution import ExecutionOutbox
from app.services.execution import ExecutionProvider

log = logging.getLogger("collab.sweeper")

START_TIMEOUT = dt.timedelta(minutes=15)
ACTIVITY_TIMEOUT = dt.timedelta(minutes=5)
MAX_RUN = dt.timedelta(minutes=40)
STOP_LOOKBACK = dt.timedelta(hours=2)  # older finished runs are not worth another stop attempt

NONTERMINAL = (
    InvocationStatus.QUEUED.value,
    InvocationStatus.DELIVERED.value,
    InvocationStatus.ACCEPTED.value,
    InvocationStatus.RUNNING.value,
    InvocationStatus.AWAITING_APPROVAL.value,
)
MESSAGES = {
    "start_timeout": "The agent did not start in time.",
    "heartbeat_lost": "The agent stopped reporting progress.",
    "timed_out": "The agent ran longer than the time limit.",
}


def _aware(value: dt.datetime) -> dt.datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=dt.UTC)


def stuck_reason(invocation: AgentInvocation, now: dt.datetime) -> str | None:
    created = _aware(invocation.created_at)
    if now - created > MAX_RUN:
        return "timed_out"
    last = _aware(invocation.last_activity_at or invocation.created_at)
    if invocation.status in (InvocationStatus.RUNNING.value, InvocationStatus.AWAITING_APPROVAL.value):
        if now - last > ACTIVITY_TIMEOUT:
            return "heartbeat_lost"
    elif now - last > START_TIMEOUT:
        return "start_timeout"
    return None


def sweep_runs(
    session_factory: sessionmaker[Session], provider: ExecutionProvider, *, now: dt.datetime | None = None
) -> int:
    """One pass. Returns the number of runs failed or stopped."""
    now = now or utcnow()
    failed = 0
    with session_factory() as db:
        candidates = db.scalars(select(AgentInvocation).where(AgentInvocation.status.in_(NONTERMINAL)).limit(200)).all()
        for invocation in candidates:
            agent = db.get(Agent, invocation.agent_id)
            if agent is None or agent.device_id is not None:
                continue  # bridge run
            reason = stuck_reason(invocation, now)
            if reason is None:
                continue
            invocation.status = InvocationStatus.FAILED.value
            invocation.error_code = reason
            invocation.error_message = MESSAGES[reason]
            invocation.completed_at = now
            outbox = db.scalar(select(ExecutionOutbox).where(ExecutionOutbox.invocation_id == invocation.id))
            if outbox is not None and outbox.status in ("pending", "claimed"):
                outbox.status = "done"  # never start it now
                outbox.lease_owner = None
                outbox.lease_expires_at = None
                outbox.completed_at = now
            log.warning("run failed by sweeper", extra={"invocation_id": str(invocation.id), "reason": reason})
            failed += 1
        db.commit()

    for outbox_id, execution_name in _executions_to_stop(session_factory, now):
        try:
            provider.cancel(execution_name)
        except Exception as error:  # retried on the next pass; the message carries no secrets
            log.warning("stop request failed: %s", type(error).__name__)
            continue
        with session_factory() as db:
            row = db.get(ExecutionOutbox, outbox_id)
            if row is not None:
                row.cancel_sent_at = now
                db.commit()
        failed += 1
    return failed


def _executions_to_stop(session_factory: sessionmaker[Session], now: dt.datetime) -> list[tuple[uuid.UUID, str]]:
    with session_factory() as db:
        rows = db.execute(
            select(ExecutionOutbox.id, ExecutionOutbox.provider_execution_id, AgentInvocation.completed_at)
            .join(AgentInvocation, AgentInvocation.id == ExecutionOutbox.invocation_id)
            .where(
                ExecutionOutbox.provider_execution_id.is_not(None),
                ExecutionOutbox.cancel_sent_at.is_(None),
                AgentInvocation.status.in_((InvocationStatus.CANCELLED.value, InvocationStatus.FAILED.value)),
            )
            .limit(100)
        ).all()
    result: list[tuple[uuid.UUID, str]] = []
    for outbox_id, execution_name, completed_at in rows:
        if completed_at is not None and now - _aware(completed_at) > STOP_LOOKBACK:
            continue
        result.append((outbox_id, execution_name))
    return result


__all__ = ["MAX_RUN", "START_TIMEOUT", "TERMINAL_INVOCATION_STATUSES", "stuck_reason", "sweep_runs"]
