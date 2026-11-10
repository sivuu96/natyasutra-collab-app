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
    # Set once the provider has been asked to stop this run, so the stop is sent exactly once.
    cancel_sent_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class InvocationLease(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One-use credential for one run attempt. Only the SHA-256 of the token is stored.

    The job exchanges the token once (see app.services.leases). The exchange marks `used_at`, and a second
    exchange fails. Expiry and revocation are checked on every exchange.
    """

    __tablename__ = "invocation_leases"
    __table_args__ = (UniqueConstraint("token_hash", name="uq_invocation_leases_token_hash"),)

    invocation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agent_invocations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Set when the provider reports the execution. An exchange must present the same name.
    provider_execution_id: Mapped[str | None] = mapped_column(String(200), nullable=True)


class RunEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Sequence log of runtime events. Text is not stored here: messages are stored where they belong.

    The unique (invocation, sequence) pair makes a retried event a no-op.
    """

    __tablename__ = "run_events"
    __table_args__ = (UniqueConstraint("invocation_id", "sequence", name="uq_run_events_invocation_sequence"),)

    invocation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agent_invocations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)


__all__ = ["OUTBOX_STATUSES", "ExecutionOutbox", "InvocationLease", "RunEvent"]
