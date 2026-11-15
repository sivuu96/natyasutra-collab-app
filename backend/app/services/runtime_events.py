"""Apply events that a runtime reports about its run.

Output is live-only, a reply is persisted as a channel message or a session transcript entry, and completion or
failure ends the run.
Each event is applied at most once, keyed by (invocation, sequence).
"""

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.agents import Agent, AgentInvocation
from app.models.enums import TERMINAL_INVOCATION_STATUSES, InvocationStatus
from app.models.execution import RunEvent
from app.models.sessions import AgentSession, AgentSessionMessage
from app.models.work import Message
from app.services.agents import _project_envelope, session_scope


class RunEventIn(BaseModel):
    sequence: int = Field(ge=0, le=1_000_000)
    type: Literal["started", "progress", "heartbeat", "output", "message", "completed", "failed"]
    text: str | None = Field(default=None, max_length=20_000)
    code: str | None = Field(default=None, max_length=60)
    message: str | None = Field(default=None, max_length=500)


class RunEventError(Exception):
    def __init__(self, status_code: int, code: str) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code


Publish = tuple[uuid.UUID, dict[str, Any]]


def apply_runtime_event(db: Session, invocation_id: uuid.UUID, event: RunEventIn) -> tuple[bool, list[Publish]]:
    """Apply one event. Returns (duplicate, realtime events to publish after commit)."""
    invocation = db.get(AgentInvocation, invocation_id)
    if invocation is None:
        raise RunEventError(404, "run_not_found")
    if invocation.status in {s.value for s in TERMINAL_INVOCATION_STATUSES}:
        raise RunEventError(409, "run_finished")

    already = db.scalar(
        select(RunEvent.id).where(RunEvent.invocation_id == invocation_id, RunEvent.sequence == event.sequence)
    )
    if already is not None:
        return True, []
    db.add(RunEvent(invocation_id=invocation_id, sequence=event.sequence, event_type=event.type))
    invocation.last_activity_at = utcnow()  # every event, including heartbeats, proves the run is alive

    agent = db.get(Agent, invocation.agent_id)
    if agent is None:
        raise RunEventError(409, "run_not_active")
    project_id = invocation.project_id
    publish: list[Publish] = []

    if event.type == "started":
        _mark_running(invocation)
    elif event.type == "heartbeat":
        pass  # liveness only; nothing to show
    elif event.type == "progress":
        _mark_running(invocation)
        scope = session_scope(db, invocation)
        publish.append(
            (
                project_id,
                {
                    "event_id": str(uuid.uuid4()),
                    "schema_version": 1,
                    "event_type": "agent.invocation.progress",
                    "project_id": str(project_id),
                    "sequence": None,
                    "occurred_at": utcnow().isoformat(),
                    "actor_user_id": None,
                    **{key: scope.get(key) for key in ("visibility", "owner_user_id", "session_id")},
                    "payload": {
                        "invocation_id": str(invocation.id),
                        "agent_handle": agent.handle,
                        "stage": (event.text or "")[:200],
                    },
                },
            )
        )
    elif event.type == "output":
        _mark_running(invocation)
        scope = session_scope(db, invocation)
        publish.append(
            (
                project_id,
                {
                    "event_id": str(uuid.uuid4()),
                    "schema_version": 1,
                    "event_type": "agent.output.delta",
                    "project_id": str(project_id),
                    "sequence": None,
                    "occurred_at": utcnow().isoformat(),
                    "actor_user_id": None,
                    **{key: scope.get(key) for key in ("visibility", "owner_user_id", "session_id")},
                    "payload": {
                        "invocation_id": str(invocation.id),
                        "agent_handle": agent.handle,
                        "text": event.text or "",
                    },
                },
            )
        )
    elif event.type == "message":
        _mark_running(invocation)
        publish.extend(_persist_reply(db, invocation, agent, event.text or ""))
    elif event.type == "completed":
        invocation.status = InvocationStatus.COMPLETED.value
        invocation.completed_at = utcnow()
        publish.append(
            (
                project_id,
                _project_envelope(
                    db,
                    project_id,
                    "agent.invocation.completed",
                    None,
                    {"invocation_id": str(invocation.id), "agent_handle": agent.handle},
                ),
            )
        )
    elif event.type == "failed":
        invocation.status = InvocationStatus.FAILED.value
        invocation.error_code = event.code or "runtime_failed"
        invocation.error_message = event.message
        invocation.completed_at = utcnow()
        publish.append(
            (
                project_id,
                _project_envelope(
                    db,
                    project_id,
                    "agent.invocation.failed",
                    None,
                    {
                        "invocation_id": str(invocation.id),
                        "agent_handle": agent.handle,
                        "error_code": invocation.error_code,
                    },
                ),
            )
        )
    return False, publish


def _mark_running(invocation: AgentInvocation) -> None:
    if invocation.status in (InvocationStatus.QUEUED.value, InvocationStatus.ACCEPTED.value):
        invocation.status = InvocationStatus.RUNNING.value


def _persist_reply(db: Session, invocation: AgentInvocation, agent: Agent, text: str) -> list[Publish]:
    """A reply goes to the session transcript for session turns, and to the channel otherwise."""
    if invocation.session_id is not None:
        session_message = AgentSessionMessage(
            session_id=invocation.session_id,
            project_id=invocation.project_id,
            role="agent",
            agent_id=agent.id,
            invocation_id=invocation.id,
            body=text,
        )
        db.add(session_message)
        db.flush()
        session = db.get(AgentSession, invocation.session_id)
        if session is not None:
            session.last_activity_at = utcnow()
        invocation.result_message_id = None
        return [
            (
                invocation.project_id,
                _project_envelope(
                    db,
                    invocation.project_id,
                    "session.message.created",
                    None,
                    {
                        "message_id": str(session_message.id),
                        "session_id": str(invocation.session_id),
                        "invocation_id": str(invocation.id),
                        "agent_handle": agent.handle,
                    },
                ),
            )
        ]

    message = Message(
        project_id=invocation.project_id,
        channel_id=invocation.channel_id,
        parent_message_id=invocation.trigger_message_id,
        author_user_id=agent.owner_user_id,
        author_agent_id=agent.id,
        body=text,
    )
    db.add(message)
    db.flush()
    invocation.result_message_id = message.id
    return [
        (
            invocation.project_id,
            _project_envelope(
                db,
                invocation.project_id,
                "conversation.message.created",
                None,
                {
                    "message_id": str(message.id),
                    "channel_id": str(invocation.channel_id),
                    "parent_message_id": str(invocation.trigger_message_id) if invocation.trigger_message_id else None,
                    "agent_handle": agent.handle,
                    "mentions": [],
                },
            ),
        )
    ]


__all__ = ["RunEventError", "RunEventIn", "apply_runtime_event"]
