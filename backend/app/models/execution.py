"""Execution outbox: durable work items that start a run on an execution provider.

A row is written in the same transaction as the invocation it dispatches, so a crash cannot leave a queued
invocation with no way to start. Workers claim rows with a lease; the provider call happens outside any
database transaction. `dedupe_key` is also sent to the provider as its idempotency key, so a retried or
duplicated submit cannot start a second run.
"""

import datetime as dt
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

OUTBOX_STATUSES = ("pending", "claimed", "done", "dead")


class ExecutionOutbox(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "execution_outbox"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_execution_outbox_dedupe_key"),
        CheckConstraint("status IN ('pending', 'claimed', 'done', 'dead')", name="status_valid"),
    )

    invocation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agent_invocations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False, default="dispatch")
    dedupe_key: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(80), nullable=True)
    lease_expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider_execution_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


__all__ = ["OUTBOX_STATUSES", "ExecutionOutbox"]
