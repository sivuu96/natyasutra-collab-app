"""Stuck-run sweeper for cloud agent runs.

It fails cloud runs that have gone quiet, with a reason the UI can show:
- start_timeout: never started (no exchange, no event) within START_TIMEOUT
- heartbeat_lost: started, but no heartbeat or event within ACTIVITY_TIMEOUT
- timed_out: still open MAX_RUN after creation

Cancelled runs need no sweep: the runtime sees run_finished at its next heartbeat and stops the agent.
"""

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import utcnow
from app.models.agents import Agent, AgentInvocation
from app.models.enums import TERMINAL_INVOCATION_STATUSES, InvocationStatus
from app.models.execution import ExecutionOutbox

log = logging.getLogger("collab.sweeper")

START_TIMEOUT = dt.timedelta(minutes=15)
ACTIVITY_TIMEOUT = dt.timedelta(minutes=5)
MAX_RUN = dt.timedelta(minutes=40)

NONTERMINAL = (
    InvocationStatus.QUEUED.value,
    InvocationStatus.ACCEPTED.value,
    InvocationStatus.RUNNING.value,
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
    if invocation.status == InvocationStatus.RUNNING.value:
        if now - last > ACTIVITY_TIMEOUT:
            return "heartbeat_lost"
    elif now - last > START_TIMEOUT:
        return "start_timeout"
    return None


def sweep_runs(session_factory: sessionmaker[Session], *, now: dt.datetime | None = None) -> int:
    """One pass. Returns the number of runs failed."""
    now = now or utcnow()
    failed = 0
    with session_factory() as db:
        candidates = db.scalars(select(AgentInvocation).where(AgentInvocation.status.in_(NONTERMINAL)).limit(200)).all()
        for invocation in candidates:
            agent = db.get(Agent, invocation.agent_id)
            if agent is None:
                continue
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

    return failed


__all__ = ["MAX_RUN", "START_TIMEOUT", "TERMINAL_INVOCATION_STATUSES", "stuck_reason", "sweep_runs"]
