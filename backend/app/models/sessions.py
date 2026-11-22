"""Dedicated agent sessions: a private or shared conversation with one attached agent."""

import datetime as dt
import uuid

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    Uuid,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import SessionStatus, SessionVisibility, check_in


class AgentSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_sessions"
    __table_args__ = (
        CheckConstraint(check_in("visibility", SessionVisibility), name="visibility_valid"),
        CheckConstraint(check_in("status", SessionStatus), name="status_valid"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    visibility: Mapped[str] = mapped_column(String(10), nullable=False, default=SessionVisibility.PRIVATE.value)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default=SessionStatus.ACTIVE.value)
    last_activity_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Set when the session is deleted. Deleted sessions are hidden everywhere; the row is kept.
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentSessionMessage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One turn of a session transcript. Only readers allowed to see the session can read these."""

    __tablename__ = "agent_session_messages"
    __table_args__ = (CheckConstraint("role IN ('user', 'agent')", name="role_valid"),)

    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agent_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(10), nullable=False)
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    invocation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agent_invocations.id", ondelete="SET NULL"), nullable=True
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)


class SessionWorkspaceTurn(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The working folder after one saved turn of a session: its files, what changed, and the diff.

    Written by the runtime after each successful turn. Readers must be allowed to see the session.
    """

    __tablename__ = "session_workspace_turns"

    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agent_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    invocation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agent_invocations.id", ondelete="SET NULL"), nullable=True
    )
    file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    total_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default="0")
    files: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    changes: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    diff: Mapped[str | None] = mapped_column(Text, nullable=True)
    diff_truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
