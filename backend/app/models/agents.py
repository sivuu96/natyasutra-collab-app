"""Agents, their project attachments, and the invocations (runs) they perform."""

import datetime as dt
import uuid

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import HarnessType, InvocationStatus, check_in


class Agent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A cloud agent. Handles are unique across the hub. Runs clone the agent's repository read-only."""

    __tablename__ = "agents"
    __table_args__ = (
        UniqueConstraint("handle", name="uq_agents_handle"),
        CheckConstraint(check_in("harness", HarnessType), name="harness_valid"),
    )

    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    handle: Mapped[str] = mapped_column(String(40), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    harness: Mapped[str] = mapped_column(String(20), nullable=False)
    # The repository a run clones and the branch it checks out. Must be on the hub allow-list.
    repository_full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    default_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
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
    """One run of an agent: from a channel mention or a dedicated session turn."""

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
    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Last time the runtime reported in (exchange, event, or heartbeat). Used to detect stuck runs.
    last_activity_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


__all__ = ["Agent", "AgentInvocation", "ProjectAgent"]


class InvocationFile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A text file an agent returned in its reply. It belongs to one run and is readable by whoever can see that run."""

    __tablename__ = "invocation_files"
    __table_args__ = (UniqueConstraint("invocation_id", "name", name="uq_invocation_files_name"),)

    invocation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agent_invocations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
