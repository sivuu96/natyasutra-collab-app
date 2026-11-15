"""Mention routing, agent runs and cancellation.

Every agent is a cloud agent: a run is queued in the outbox, started on the execution provider, and reported back
by its runtime. Invariants enforced here:
- A run is created in the same transaction as the message or turn that asked for it.
- Output and status events are persisted (project_events) before they are broadcast.
- Agent authorship is recorded on messages (author_agent_id) and agent identity never grants project access.
"""

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models import (
    Agent,
    AgentInvocation,
    AgentSession,
    Channel,
    Message,
    ProjectAgent,
    User,
)
from app.models.enums import TERMINAL_INVOCATION_STATUSES, InvocationStatus
from app.realtime.hub import hub
from app.services.audit import record_audit
from app.services.events import record_event
from app.services.execution import enqueue_dispatch

MENTION_RE = re.compile(r"(?<![\w@])@([a-z0-9][a-z0-9-]{2,39})")


@dataclass
class Outcome:
    """What a handler wants to happen after its transaction commits."""

    publish: list[tuple[uuid.UUID, dict[str, Any]]] = field(default_factory=list)  # (project_id, envelope)


def mentioned_handles(body: str) -> list[str]:
    return sorted(set(MENTION_RE.findall(body.lower())))


def session_scope(db: Session, invocation: AgentInvocation) -> dict[str, Any]:
    """Visibility metadata for events about a session turn. Empty for channel invocations."""
    if invocation.session_id is None:
        return {}
    session = db.get(AgentSession, invocation.session_id)
    if session is None:
        return {}
    return {
        "session_id": str(session.id),
        "visibility": session.visibility,
        "owner_user_id": str(session.owner_user_id),
    }


def _project_envelope(
    db: Session, project_id: uuid.UUID, event_type: str, actor: uuid.UUID | None, payload: dict[str, Any]
) -> dict[str, Any]:
    invocation_id = payload.get("invocation_id")
    if invocation_id and "visibility" not in payload:
        invocation = db.get(AgentInvocation, uuid.UUID(str(invocation_id)))
        if invocation is not None:
            payload = {**payload, **session_scope(db, invocation)}
    return record_event(db, project_id=project_id, event_type=event_type, actor_user_id=actor, payload=payload)


# ---- creating runs -------------------------------------------------------------------------------


def create_invocations_for_message(
    db: Session,
    *,
    project_id: uuid.UUID,
    channel: Channel,
    message: Message,
    requester: User,
    outcome: Outcome,
) -> list[uuid.UUID]:
    """Called inside the message-creation transaction. Returns the new invocation ids.

    Each mention of an attached agent becomes a queued run. Creation envelopes are appended to `outcome.publish`
    and broadcast after commit.
    """
    created: list[uuid.UUID] = []
    for handle in mentioned_handles(message.body):
        agent = db.scalar(select(Agent).where(Agent.handle == handle, Agent.revoked_at.is_(None)))
        if agent is None:
            continue
        attached = db.scalar(
            select(ProjectAgent.id).where(ProjectAgent.project_id == project_id, ProjectAgent.agent_id == agent.id)
        )
        if attached is None:
            continue  # not attached to this project: mention is plain text

        invocation = AgentInvocation(
            project_id=project_id,
            agent_id=agent.id,
            channel_id=channel.id,
            trigger_message_id=message.id,
            requested_by_user_id=requester.id,
            status=InvocationStatus.QUEUED.value,
            prompt=message.body[:10000],
        )
        db.add(invocation)
        db.flush()
        enqueue_dispatch(db, invocation.id)  # same transaction as the invocation
        record_audit(
            db,
            action="agent.invocation.created",
            actor_user_id=requester.id,
            project_id=project_id,
            target_type="agent_invocation",
            target_id=str(invocation.id),
        )
        outcome.publish.append(
            (
                project_id,
                _project_envelope(
                    db,
                    project_id,
                    "agent.invocation.created",
                    requester.id,
                    {
                        "invocation_id": str(invocation.id),
                        "agent_handle": agent.handle,
                        "status": invocation.status,
                        "channel_id": str(channel.id),
                    },
                ),
            )
        )
        created.append(invocation.id)
    return created


def create_session_turn(
    db: Session,
    *,
    session: AgentSession,
    body: str,
    requester: User,
    outcome: Outcome,
) -> uuid.UUID:
    """One turn of a dedicated session. The run carries the session's earlier turns (see services/transcript.py)."""
    agent = db.get(Agent, session.agent_id)
    if agent is None or agent.revoked_at is not None:
        raise ValueError("agent is no longer available")
    invocation = AgentInvocation(
        project_id=session.project_id,
        agent_id=agent.id,
        channel_id=None,
        session_id=session.id,
        requested_by_user_id=requester.id,
        status=InvocationStatus.QUEUED.value,
        prompt=body[:10000],
    )
    db.add(invocation)
    db.flush()
    enqueue_dispatch(db, invocation.id)
    session.last_activity_at = utcnow()
    record_audit(
        db,
        action="agent.invocation.created",
        actor_user_id=requester.id,
        project_id=session.project_id,
        target_type="agent_invocation",
        target_id=str(invocation.id),
    )
    outcome.publish.append(
        (
            session.project_id,
            _project_envelope(
                db,
                session.project_id,
                "agent.invocation.created",
                requester.id,
                {"invocation_id": str(invocation.id), "agent_handle": agent.handle, "status": invocation.status},
            ),
        )
    )
    return invocation.id


# ---- cancellation --------------------------------------------------------------------------------


def _set_status(invocation: AgentInvocation, status: InvocationStatus) -> None:
    invocation.status = status.value
    invocation.completed_at = utcnow()


def request_cancel(db: Session, invocation: AgentInvocation, actor: User) -> Outcome:
    """Mark the run cancelled. The runtime stops at its next heartbeat, when the hub refuses it."""
    outcome = Outcome()
    if invocation.status in {s.value for s in TERMINAL_INVOCATION_STATUSES}:
        return outcome
    _set_status(invocation, InvocationStatus.CANCELLED)
    record_audit(
        db,
        action="agent.invocation.cancel_requested",
        actor_user_id=actor.id,
        project_id=invocation.project_id,
        target_type="agent_invocation",
        target_id=str(invocation.id),
    )
    outcome.publish.append(
        (
            invocation.project_id,
            _project_envelope(
                db,
                invocation.project_id,
                "agent.invocation.cancel_requested",
                actor.id,
                {"invocation_id": str(invocation.id), "status": invocation.status},
            ),
        )
    )
    return outcome


def apply_outcome(outcome: Outcome) -> None:
    """Run after commit: broadcast envelopes to subscribers."""
    for project_id, envelope in outcome.publish:
        hub.publish(project_id, envelope)
