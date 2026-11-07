import datetime as dt
import uuid

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import DecisionStatus, TaskPriority, TaskStatus, check_in


class Message(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "messages"

    # project_id is denormalised from the channel so every query can be project-scoped directly.
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("channels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parent_message_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("messages.id", ondelete="SET NULL"), nullable=True, index=True
    )
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Set when an agent wrote the message (its output). author_user_id is then the agent's owner.
    author_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    edited_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    deleted_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)


class Task(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(check_in("status", TaskStatus), name="status_valid"),
        CheckConstraint(check_in("priority", TaskPriority), name="priority_valid"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    acceptance_criteria: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=TaskStatus.BACKLOG.value, index=True)
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default=TaskPriority.MEDIUM.value)
    creator_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    assignee_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    due_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)


class Decision(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "decisions"
    __table_args__ = (CheckConstraint(check_in("status", DecisionStatus), name="status_valid"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[str | None] = mapped_column(Text, nullable=True)
    options: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False, default=list)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    consequences: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=DecisionStatus.PROPOSED.value, index=True)
    proposer_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    current_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class DecisionRevision(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "decision_revisions"
    __table_args__ = (UniqueConstraint("decision_id", "revision_no", name="uq_decision_revisions_decision_rev"),)

    decision_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("decisions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    changed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
