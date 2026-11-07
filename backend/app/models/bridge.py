"""Bridge devices, pairing, agents, invocations, approvals and inbound agent events."""

import datetime as dt
import uuid

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, utcnow
from app.models.enums import ApprovalStatus, HarnessType, InvocationStatus, check_in


class BridgeDevice(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A developer machine paired with the hub. The device secret is stored only as a hash."""

    __tablename__ = "bridge_devices"

    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    secret_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    last_seen_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PairingCode(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One-time code shown to the user; only its hash is stored."""

    __tablename__ = "bridge_pairing_codes"

    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    redeemed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("bridge_devices.id", ondelete="SET NULL"), nullable=True
    )


class Agent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A named local agent registered by a device owner. Handles are unique across the hub."""

    __tablename__ = "agents"
    __table_args__ = (
        UniqueConstraint("handle", name="uq_agents_handle"),
        CheckConstraint(check_in("harness", HarnessType), name="harness_valid"),
    )

    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("bridge_devices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    handle: Mapped[str] = mapped_column(String(40), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    harness: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    capabilities: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ProjectAgent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An agent attached to a project. Attaching is the owner's consent for that project."""

    __tablename__ = "project_agents"
    __table_args__ = (UniqueConstraint("project_id", "agent_id", name="uq_project_agents_project_agent"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    added_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class AgentInvocation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_invocations"
    __table_args__ = (CheckConstraint(check_in("status", InvocationStatus), name="status_valid"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Null for dedicated-session turns, which belong to a session rather than a channel.
    channel_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("channels.id", ondelete="CASCADE"), nullable=True
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agent_sessions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    trigger_message_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default=InvocationStatus.QUEUED.value, index=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    queued_if_offline: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    harness_session_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    result_message_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    delivered_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentApproval(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_approvals"
    __table_args__ = (CheckConstraint(check_in("status", ApprovalStatus), name="status_valid"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    invocation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agent_invocations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False)
    operation: Mapped[str] = mapped_column(String(80), nullable=False)
    tools: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    detail: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ApprovalStatus.PENDING.value, index=True)
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentEvent(Base):
    """Inbound bridge frames, persisted before processing. The event_id primary key makes ingestion idempotent."""

    __tablename__ = "agent_events"

    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    device_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("bridge_devices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    invocation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agent_invocations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    received_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


__all__ = [
    "Agent",
    "AgentApproval",
    "AgentEvent",
    "AgentInvocation",
    "BridgeDevice",
    "PairingCode",
    "ProjectAgent",
]
