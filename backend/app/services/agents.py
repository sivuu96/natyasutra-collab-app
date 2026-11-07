"""Agent registry, mention routing, invocation lifecycle and approvals.

Invariants enforced here:
- A bridge can only change invocations and approvals belonging to agents on its own device.
- An invocation leaves `queued` only when a live bridge takes it. Offline agents are never reported as running.
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
    AgentApproval,
    AgentEvent,
    AgentInvocation,
    AgentSession,
    AgentSessionMessage,
    BridgeDevice,
    Channel,
    Message,
    ProjectAgent,
    User,
)
from app.models.enums import (
    TERMINAL_INVOCATION_STATUSES,
    ApprovalStatus,
    AuditOutcome,
    InvocationStatus,
)
from app.realtime.bridge_registry import bridge_registry
from app.realtime.hub import hub
from app.services.audit import record_audit
from app.services.bridge_protocol import HANDLE_PATTERN, PROTOCOL_VERSION, HelloPayload, InvocationFailed
from app.services.events import record_event

TERMINAL_VALUES = frozenset(status.value for status in TERMINAL_INVOCATION_STATUSES)

ACTIVE_STATUSES = frozenset(
    {
        InvocationStatus.DELIVERED,
        InvocationStatus.ACCEPTED,
        InvocationStatus.RUNNING,
        InvocationStatus.AWAITING_APPROVAL,
    }
)
HANDLE_RE = re.compile(HANDLE_PATTERN)
MENTION_RE = re.compile(r"(?<![\w@])@([a-z0-9][a-z0-9-]{2,39})")


@dataclass
class Outcome:
    """What a handler wants to happen after its transaction commits."""

    frames: list[tuple[uuid.UUID, dict[str, Any]]] = field(default_factory=list)  # (device_id, frame)
    publish: list[tuple[uuid.UUID, dict[str, Any]]] = field(default_factory=list)  # (project_id, envelope)


def mentioned_handles(body: str) -> list[str]:
    return sorted(set(MENTION_RE.findall(body.lower())))


def device_is_online(device: BridgeDevice) -> bool:
    return device.revoked_at is None and bridge_registry.is_online(device.id)


# ---- frames sent to bridges ----------------------------------------------------------------------


def _frame(frame_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"type": frame_type, "event_id": str(uuid.uuid4()), "payload": payload}


def _invocation_created_frame(invocation: AgentInvocation, agent: Agent, requester: User | None) -> dict[str, Any]:
    return _frame(
        "invocation.created",
        {
            "invocation_id": str(invocation.id),
            "project_id": str(invocation.project_id),
            "agent_handle": agent.handle,
            "prompt": invocation.prompt,
            "requested_by": requester.display_name if requester else "a project member",
            "harness_session_ref": invocation.harness_session_ref,
        },
    )


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


# ---- registry ------------------------------------------------------------------------------------


def upsert_agents_from_hello(db: Session, device: BridgeDevice, hello: HelloPayload) -> tuple[list[Agent], list[str]]:
    """Returns (registered agents, handles refused because they are invalid or owned by another user)."""
    agents: list[Agent] = []
    refused: list[str] = []
    for descriptor in hello.agents:
        if not HANDLE_RE.match(descriptor.handle):
            refused.append(descriptor.handle)
            continue
        existing = db.scalar(select(Agent).where(Agent.handle == descriptor.handle))
        if existing is not None and existing.owner_user_id != device.owner_user_id:
            # Handles are global. Another user already owns this one; never take it over.
            refused.append(descriptor.handle)
            record_audit(
                db,
                action="agent.register_denied",
                actor_user_id=device.owner_user_id,
                target_type="agent",
                target_id=descriptor.handle,
                outcome=AuditOutcome.DENIED,
            )
            continue
        if existing is None:
            existing = Agent(
                owner_user_id=device.owner_user_id,
                device_id=device.id,
                handle=descriptor.handle,
                display_name=descriptor.display_name,
                harness=descriptor.harness,
                description=descriptor.description,
                capabilities=list(descriptor.capabilities),
            )
            db.add(existing)
            db.flush()
            record_audit(
                db,
                action="agent.registered",
                actor_user_id=device.owner_user_id,
                target_type="agent",
                target_id=str(existing.id),
            )
        else:
            existing.device_id = device.id
            existing.display_name = descriptor.display_name
            existing.harness = descriptor.harness
            existing.description = descriptor.description
            existing.capabilities = list(descriptor.capabilities)
            existing.revoked_at = None
        agents.append(existing)
    return agents, refused


def attached_project_ids(db: Session, agent_id: uuid.UUID) -> list[uuid.UUID]:
    return list(db.scalars(select(ProjectAgent.project_id).where(ProjectAgent.agent_id == agent_id)).all())


# ---- routing from messages -----------------------------------------------------------------------


def create_invocations_for_message(
    db: Session,
    *,
    project_id: uuid.UUID,
    channel: Channel,
    message: Message,
    requester: User,
    queue_if_offline: bool,
    outcome: Outcome,
) -> list[uuid.UUID]:
    """Called inside the message-creation transaction. Returns invocation ids to dispatch after commit.

    Creation envelopes are appended to `outcome.publish` and broadcast after commit.
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
        device = db.get(BridgeDevice, agent.device_id)
        online = device is not None and device_is_online(device)
        if online or queue_if_offline:
            status, error_code = InvocationStatus.QUEUED, None
        else:
            status, error_code = InvocationStatus.REJECTED, "agent_offline"

        invocation = AgentInvocation(
            project_id=project_id,
            agent_id=agent.id,
            channel_id=channel.id,
            trigger_message_id=message.id,
            requested_by_user_id=requester.id,
            status=status.value,
            prompt=message.body[:10000],
            queued_if_offline=queue_if_offline,
            error_code=error_code,
            completed_at=utcnow() if status is InvocationStatus.REJECTED else None,
        )
        db.add(invocation)
        db.flush()
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
                        "status": status.value,
                        "error_code": error_code,
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
    queue_if_offline: bool,
    outcome: Outcome,
) -> uuid.UUID:
    """One turn of a dedicated session. Same offline rules as a mention; resumes the session's harness thread."""
    agent = db.get(Agent, session.agent_id)
    if agent is None or agent.revoked_at is not None:
        raise ValueError("agent is no longer available")
    device = db.get(BridgeDevice, agent.device_id)
    online = device is not None and device_is_online(device)
    if online or queue_if_offline:
        status, error_code = InvocationStatus.QUEUED, None
    else:
        status, error_code = InvocationStatus.REJECTED, "agent_offline"
    invocation = AgentInvocation(
        project_id=session.project_id,
        agent_id=agent.id,
        channel_id=None,
        session_id=session.id,
        requested_by_user_id=requester.id,
        status=status.value,
        prompt=body[:10000],
        queued_if_offline=queue_if_offline,
        harness_session_ref=session.harness_session_ref,
        error_code=error_code,
        completed_at=utcnow() if status is InvocationStatus.REJECTED else None,
    )
    db.add(invocation)
    db.flush()
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
                {
                    "invocation_id": str(invocation.id),
                    "agent_handle": agent.handle,
                    "status": status.value,
                    "error_code": error_code,
                },
            ),
        )
    )
    return invocation.id


def dispatch_invocations(db: Session, invocation_ids: list[uuid.UUID]) -> Outcome:
    """Send invocations to their bridges. Call after the creating transaction has committed, then commit again."""
    outcome = Outcome()
    for invocation_id in invocation_ids:
        invocation = db.get(AgentInvocation, invocation_id)
        if invocation is None or invocation.status != InvocationStatus.QUEUED.value:
            continue
        agent = db.get(Agent, invocation.agent_id)
        device = db.get(BridgeDevice, agent.device_id) if agent else None
        if agent is None or device is None or not device_is_online(device):
            continue  # stays queued (queue_if_offline) until the device connects
        requester = db.get(User, invocation.requested_by_user_id) if invocation.requested_by_user_id else None
        frame = _invocation_created_frame(invocation, agent, requester)
        if not bridge_registry.send(device.id, frame):
            continue
        invocation.status = InvocationStatus.DELIVERED.value
        invocation.delivered_at = utcnow()
        outcome.publish.append(
            (
                invocation.project_id,
                _project_envelope(
                    db,
                    invocation.project_id,
                    "agent.invocation.delivered",
                    None,
                    {"invocation_id": str(invocation.id), "agent_handle": agent.handle, "status": "delivered"},
                ),
            )
        )
    return outcome


def flush_pending_for_device(db: Session, device: BridgeDevice) -> Outcome:
    """On bridge connect: deliver queued work and re-send resolved approvals the bridge has not acted on."""
    outcome = Outcome()
    agent_ids = list(db.scalars(select(Agent.id).where(Agent.device_id == device.id)).all())
    if not agent_ids:
        return outcome
    queued = list(
        db.scalars(
            select(AgentInvocation.id)
            .where(AgentInvocation.agent_id.in_(agent_ids), AgentInvocation.status == InvocationStatus.QUEUED.value)
            .order_by(AgentInvocation.created_at)
        ).all()
    )
    outcome = dispatch_invocations(db, queued)

    pending_resolutions = db.scalars(
        select(AgentApproval)
        .join(AgentInvocation, AgentInvocation.id == AgentApproval.invocation_id)
        .where(
            AgentApproval.agent_id.in_(agent_ids),
            AgentApproval.status != ApprovalStatus.PENDING.value,
            AgentInvocation.status == InvocationStatus.AWAITING_APPROVAL.value,
        )
    ).all()
    for approval in pending_resolutions:
        outcome.frames.append(
            (
                device.id,
                _frame(
                    "approval.resolved",
                    {
                        "invocation_id": str(approval.invocation_id),
                        "approval_id": str(approval.id),
                        "decision": approval.status,
                    },
                ),
            )
        )
    return outcome


def agent_status_outcome(db: Session, device: BridgeDevice, online: bool) -> Outcome:
    outcome = Outcome()
    agents = db.scalars(select(Agent).where(Agent.device_id == device.id, Agent.revoked_at.is_(None))).all()
    for agent in agents:
        for project_id in attached_project_ids(db, agent.id):
            outcome.publish.append(
                (
                    project_id,
                    _project_envelope(
                        db,
                        project_id,
                        "agent.status.changed",
                        device.owner_user_id,
                        {"agent_handle": agent.handle, "online": online},
                    ),
                )
            )
    return outcome


# ---- inbound frames ------------------------------------------------------------------------------


class FrameRejected(Exception):
    pass


def _load_owned_invocation(db: Session, device: BridgeDevice, invocation_id: uuid.UUID) -> AgentInvocation:
    invocation = db.get(AgentInvocation, invocation_id)
    if invocation is None:
        raise FrameRejected("unknown invocation")
    agent = db.get(Agent, invocation.agent_id)
    if agent is None or agent.device_id != device.id:
        # Do not reveal whether the invocation exists on another device.
        raise FrameRejected("unknown invocation")
    return invocation


def _set_status(invocation: AgentInvocation, status: InvocationStatus) -> None:
    invocation.status = status.value
    if status in TERMINAL_INVOCATION_STATUSES:
        invocation.completed_at = utcnow()


def apply_inbound(db: Session, device: BridgeDevice, frame_type: str, payload: Any) -> Outcome:
    """Apply one validated, non-heartbeat frame. Caller commits, then sends frames and publishes."""
    outcome = Outcome()

    if frame_type == "bridge.hello":
        _, refused = upsert_agents_from_hello(db, device, payload)
        db.flush()
        outcome = flush_pending_for_device(db, device)
        # Welcome is sent only now, after registration: receiving it means the agents are known to the hub.
        outcome.frames.insert(0, (device.id, hello_ack_frame(device.id)))
        for handle in refused:
            # Tell the bridge explicitly, so an operator is not left wondering why an agent never appears.
            outcome.frames.append(
                (
                    device.id,
                    {
                        "type": "bridge.error",
                        "event_id": str(uuid.uuid4()),
                        "payload": {
                            "code": "agent_registration_refused",
                            "detail": f"the handle {handle!r} is invalid or already belongs to another user",
                        },
                    },
                )
            )
        status = agent_status_outcome(db, device, online=True)
        outcome.publish.extend(status.publish)
        return outcome

    invocation = _load_owned_invocation(db, device, payload.invocation_id)
    agent = db.get(Agent, invocation.agent_id)
    assert agent is not None
    project_id = invocation.project_id
    actor = agent.owner_user_id

    if frame_type == "invocation.accepted":
        if invocation.status not in (InvocationStatus.DELIVERED.value, InvocationStatus.QUEUED.value):
            raise FrameRejected("invocation not awaiting acceptance")
        _set_status(invocation, InvocationStatus.ACCEPTED)
        outcome.publish.append(
            (
                project_id,
                _project_envelope(
                    db,
                    project_id,
                    "agent.invocation.accepted",
                    actor,
                    {"invocation_id": str(invocation.id), "agent_handle": agent.handle},
                ),
            )
        )
    elif frame_type == "invocation.rejected":
        if invocation.status in {s.value for s in TERMINAL_INVOCATION_STATUSES}:
            raise FrameRejected("invocation already finished")
        invocation.error_code = payload.reason_code
        invocation.error_message = payload.detail
        _set_status(invocation, InvocationStatus.REJECTED)
        outcome.publish.append(
            (
                project_id,
                _project_envelope(
                    db,
                    project_id,
                    "agent.invocation.rejected",
                    actor,
                    {
                        "invocation_id": str(invocation.id),
                        "agent_handle": agent.handle,
                        "reason_code": payload.reason_code,
                    },
                ),
            )
        )
    elif frame_type == "invocation.output_delta":
        if invocation.status in {s.value for s in TERMINAL_INVOCATION_STATUSES}:
            raise FrameRejected("invocation already finished")
        if invocation.status in (InvocationStatus.ACCEPTED.value, InvocationStatus.AWAITING_APPROVAL.value):
            _set_status(invocation, InvocationStatus.RUNNING)
        # Deltas are live-only: not persisted, not sequenced. The final message is persisted.
        # Session scope sits at the top level so realtime delivery can filter private output per viewer.
        scope = session_scope(db, invocation)
        outcome.publish.append(
            (
                project_id,
                {
                    "event_id": str(uuid.uuid4()),
                    "schema_version": 1,
                    "event_type": "agent.output.delta",
                    "project_id": str(project_id),
                    "sequence": None,
                    "occurred_at": utcnow().isoformat(),
                    "actor_user_id": str(actor),
                    **{key: scope.get(key) for key in ("visibility", "owner_user_id", "session_id")},
                    "payload": {
                        "invocation_id": str(invocation.id),
                        "agent_handle": agent.handle,
                        "text": payload.text,
                    },
                },
            )
        )
    elif frame_type == "invocation.output_message":
        if invocation.status in {s.value for s in TERMINAL_INVOCATION_STATUSES}:
            raise FrameRejected("invocation already finished")
        if invocation.session_id is not None:
            # A session turn: the reply goes to the session transcript, not to a channel.
            session_message = AgentSessionMessage(
                session_id=invocation.session_id,
                project_id=project_id,
                role="agent",
                agent_id=agent.id,
                invocation_id=invocation.id,
                body=payload.text,
            )
            db.add(session_message)
            db.flush()
            session = db.get(AgentSession, invocation.session_id)
            if session is not None:
                session.last_activity_at = utcnow()
            invocation.result_message_id = None
            if invocation.status in (
                InvocationStatus.ACCEPTED.value,
                InvocationStatus.DELIVERED.value,
                InvocationStatus.AWAITING_APPROVAL.value,
            ):
                _set_status(invocation, InvocationStatus.RUNNING)
            # The body stays out of the persisted event; readers fetch it from the transcript.
            outcome.publish.append(
                (
                    project_id,
                    _project_envelope(
                        db,
                        project_id,
                        "session.message.created",
                        actor,
                        {
                            "message_id": str(session_message.id),
                            "session_id": str(invocation.session_id),
                            "invocation_id": str(invocation.id),
                            "agent_handle": agent.handle,
                        },
                    ),
                )
            )
        else:
            message = Message(
                project_id=project_id,
                channel_id=invocation.channel_id,
                parent_message_id=invocation.trigger_message_id,
                author_user_id=agent.owner_user_id,
                author_agent_id=agent.id,
                body=payload.text,
            )
            db.add(message)
            db.flush()
            invocation.result_message_id = message.id
            if invocation.status in (
                InvocationStatus.ACCEPTED.value,
                InvocationStatus.DELIVERED.value,
                InvocationStatus.AWAITING_APPROVAL.value,
            ):
                _set_status(invocation, InvocationStatus.RUNNING)
            outcome.publish.append(
                (
                    project_id,
                    _project_envelope(
                        db,
                        project_id,
                        "conversation.message.created",
                        actor,
                        {
                            "message_id": str(message.id),
                            "channel_id": str(invocation.channel_id),
                            "parent_message_id": str(invocation.trigger_message_id)
                            if invocation.trigger_message_id
                            else None,
                            "agent_handle": agent.handle,
                            "mentions": [],
                        },
                    ),
                )
            )
    elif frame_type == "invocation.completed":
        if invocation.status in {s.value for s in TERMINAL_INVOCATION_STATUSES}:
            raise FrameRejected("invocation already finished")
        if payload.harness_session_ref:
            invocation.harness_session_ref = payload.harness_session_ref
            if invocation.session_id is not None:
                # Next turn of this session resumes the same harness thread.
                session = db.get(AgentSession, invocation.session_id)
                if session is not None:
                    session.harness_session_ref = payload.harness_session_ref
        _set_status(invocation, InvocationStatus.COMPLETED)
        outcome.publish.append(
            (
                project_id,
                _project_envelope(
                    db,
                    project_id,
                    "agent.invocation.completed",
                    actor,
                    {"invocation_id": str(invocation.id), "agent_handle": agent.handle},
                ),
            )
        )
    elif frame_type == "invocation.failed":
        if invocation.status in {s.value for s in TERMINAL_INVOCATION_STATUSES}:
            raise FrameRejected("invocation already finished")
        assert isinstance(payload, InvocationFailed)
        invocation.error_code = payload.code
        invocation.error_message = payload.message
        _set_status(invocation, InvocationStatus.FAILED)
        outcome.publish.append(
            (
                project_id,
                _project_envelope(
                    db,
                    project_id,
                    "agent.invocation.failed",
                    actor,
                    {"invocation_id": str(invocation.id), "agent_handle": agent.handle, "error_code": payload.code},
                ),
            )
        )
    elif frame_type == "invocation.cancelled":
        if invocation.status in {s.value for s in TERMINAL_INVOCATION_STATUSES}:
            raise FrameRejected("invocation already finished")
        _set_status(invocation, InvocationStatus.CANCELLED)
        outcome.publish.append(
            (
                project_id,
                _project_envelope(
                    db,
                    project_id,
                    "agent.invocation.cancelled",
                    actor,
                    {"invocation_id": str(invocation.id), "agent_handle": agent.handle},
                ),
            )
        )
    elif frame_type == "approval.required":
        if invocation.status in {s.value for s in TERMINAL_INVOCATION_STATUSES}:
            raise FrameRejected("invocation already finished")
        pending = db.scalar(
            select(AgentApproval.id).where(
                AgentApproval.invocation_id == invocation.id, AgentApproval.status == ApprovalStatus.PENDING.value
            )
        )
        if pending is not None:
            raise FrameRejected("an approval is already pending for this invocation")
        approval = AgentApproval(
            project_id=project_id,
            invocation_id=invocation.id,
            agent_id=agent.id,
            operation=payload.operation,
            tools=list(payload.tools),
            detail=payload.detail,
            status=ApprovalStatus.PENDING.value,
        )
        db.add(approval)
        db.flush()
        _set_status(invocation, InvocationStatus.AWAITING_APPROVAL)
        invocation.completed_at = None
        outcome.publish.append(
            (
                project_id,
                _project_envelope(
                    db,
                    project_id,
                    "agent.approval.required",
                    actor,
                    {
                        "approval_id": str(approval.id),
                        "invocation_id": str(invocation.id),
                        "agent_handle": agent.handle,
                        "operation": payload.operation,
                        "tools": payload.tools,
                        "detail": payload.detail,
                    },
                ),
            )
        )
    else:  # pragma: no cover - validate_inbound prevents this
        raise FrameRejected("unsupported frame")

    # Lifecycle transitions are audited. Streamed deltas are not: they would flood the log and are live-only.
    if frame_type != "invocation.output_delta":
        record_audit(
            db,
            action=f"agent.{frame_type}",
            actor_user_id=actor,
            project_id=project_id,
            target_type="agent_invocation",
            target_id=str(invocation.id),
        )
    return outcome


def record_inbound_event(
    db: Session,
    device: BridgeDevice,
    event_id: uuid.UUID,
    frame_type: str,
    invocation_id: uuid.UUID | None,
    payload_dump: dict[str, Any],
) -> bool:
    """Persist the frame before processing. Returns False for a duplicate (already processed)."""
    if db.get(AgentEvent, event_id) is not None:
        return False
    db.add(
        AgentEvent(
            event_id=event_id,
            device_id=device.id,
            invocation_id=invocation_id,
            event_type=frame_type,
            payload=payload_dump,
        )
    )
    db.flush()
    return True


def handle_disconnect(db: Session, device: BridgeDevice) -> Outcome:
    """Bridge went away. Work it was doing cannot be assumed to finish, so it is failed, not left running."""
    outcome = Outcome()
    agent_ids = list(db.scalars(select(Agent.id).where(Agent.device_id == device.id)).all())
    if agent_ids:
        active = db.scalars(
            select(AgentInvocation).where(
                AgentInvocation.agent_id.in_(agent_ids), AgentInvocation.status.in_([s.value for s in ACTIVE_STATUSES])
            )
        ).all()
        for invocation in active:
            invocation.error_code = "bridge_disconnected"
            invocation.error_message = "The local bridge disconnected before the agent finished."
            _set_status(invocation, InvocationStatus.FAILED)
            # A pending approval for work that has now ended can no longer be acted on: deny it.
            for approval in db.scalars(
                select(AgentApproval).where(
                    AgentApproval.invocation_id == invocation.id, AgentApproval.status == ApprovalStatus.PENDING.value
                )
            ).all():
                approval.status = ApprovalStatus.DENIED.value
                approval.decided_at = utcnow()
            agent = db.get(Agent, invocation.agent_id)
            outcome.publish.append(
                (
                    invocation.project_id,
                    _project_envelope(
                        db,
                        invocation.project_id,
                        "agent.invocation.failed",
                        device.owner_user_id,
                        {
                            "invocation_id": str(invocation.id),
                            "agent_handle": agent.handle if agent else None,
                            "error_code": "bridge_disconnected",
                        },
                    ),
                )
            )
    outcome.publish.extend(agent_status_outcome(db, device, online=False).publish)
    return outcome


def hello_ack_frame(device_id: uuid.UUID) -> dict[str, Any]:
    return _frame("bridge.welcome", {"protocol_version": PROTOCOL_VERSION, "device_id": str(device_id)})


def approval_resolved_frame(approval: AgentApproval) -> dict[str, Any]:
    return _frame(
        "approval.resolved",
        {"invocation_id": str(approval.invocation_id), "approval_id": str(approval.id), "decision": approval.status},
    )


def cancel_frame(invocation: AgentInvocation) -> dict[str, Any]:
    return _frame("invocation.cancel", {"invocation_id": str(invocation.id)})


def request_cancel(db: Session, invocation: AgentInvocation, actor: User) -> Outcome:
    """Queued work is cancelled here. Work a bridge already holds is cancelled by asking the bridge."""
    outcome = Outcome()
    if invocation.status in {s.value for s in TERMINAL_INVOCATION_STATUSES}:
        return outcome
    agent = db.get(Agent, invocation.agent_id)
    device = db.get(BridgeDevice, agent.device_id) if agent else None
    if invocation.status == InvocationStatus.QUEUED.value or device is None or not device_is_online(device):
        if invocation.status == InvocationStatus.QUEUED.value:
            _set_status(invocation, InvocationStatus.CANCELLED)
        else:
            invocation.error_code = "bridge_offline"
            invocation.error_message = "Cancelled while the bridge was offline."
            _set_status(invocation, InvocationStatus.FAILED)
    else:
        outcome.frames.append((device.id, cancel_frame(invocation)))
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
    """Run after commit: hand frames to bridges and broadcast envelopes to subscribers."""
    for device_id, frame in outcome.frames:
        bridge_registry.send(device_id, frame)
    for project_id, envelope in outcome.publish:
        hub.publish(project_id, envelope)
