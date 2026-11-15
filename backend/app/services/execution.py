"""Execution provider interface and the outbox worker that starts runs through it.

Rules:
- Provider calls never run inside a database transaction. The worker claims a row (committed), mints a
  one-use run lease (committed), calls the provider, then records the outcome in a further transaction.
- Every submit carries the outbox `dedupe_key` as the provider's idempotency key.
- A claim is a lease. An expired lease can be reclaimed, so a crashed worker cannot strand work.
- The run lease token is the only credential the runtime receives. Only its SHA-256 is stored.
- Failed submits back off and move to `dead` after `max_attempts`; the invocation is then marked failed.
"""

import datetime as dt
import hashlib
import secrets
import uuid
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import utcnow
from app.models.agents import AgentInvocation
from app.models.enums import TERMINAL_INVOCATION_STATUSES, InvocationStatus
from app.models.execution import ExecutionOutbox, InvocationLease

RUN_LEASE_MINUTES = 10  # how long a run has to exchange its lease after it starts


class ProviderError(Exception):
    """A provider call failed. The worker retries with backoff."""


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class ExecutionProvider(Protocol):
    def submit(self, *, invocation_id: uuid.UUID, dedupe_key: str, lease_token: str) -> str:
        """Start a run. Returns the provider's execution id. Must be idempotent on `dedupe_key`."""


@dataclass
class FakeExecutionProvider:
    """Deterministic provider for tests and local runs. Records calls; can simulate failures."""

    fail_next: int = 0
    submitted: list[tuple[uuid.UUID, str, str]] = field(default_factory=list)
    lease_tokens: dict[str, str] = field(default_factory=dict)  # execution id -> lease token it was given
    _by_key: dict[str, str] = field(default_factory=dict)

    def submit(self, *, invocation_id: uuid.UUID, dedupe_key: str, lease_token: str) -> str:
        if self.fail_next > 0:
            self.fail_next -= 1
            raise ProviderError("simulated provider failure")
        # Idempotent: the same key always returns the same execution, as a real provider would.
        if dedupe_key in self._by_key:
            existing = self._by_key[dedupe_key]
            self.lease_tokens[existing] = lease_token  # the newest lease is the one the run will hold
            return existing
        execution_id = f"fake-{uuid.uuid4().hex[:12]}"
        self._by_key[dedupe_key] = execution_id
        self.submitted.append((invocation_id, dedupe_key, execution_id))
        self.lease_tokens[execution_id] = lease_token
        return execution_id


def enqueue_dispatch(db: Session, invocation_id: uuid.UUID, *, now: dt.datetime | None = None) -> ExecutionOutbox:
    """Add the dispatch work item for an invocation. Idempotent: returns the existing row if one exists.

    Call inside the transaction that creates the invocation.
    """
    dedupe_key = f"dispatch:{invocation_id}"
    existing = db.scalar(select(ExecutionOutbox).where(ExecutionOutbox.dedupe_key == dedupe_key))
    if existing is not None:
        return existing
    row = ExecutionOutbox(
        invocation_id=invocation_id,
        kind="dispatch",
        dedupe_key=dedupe_key,
        status="pending",
        attempts=0,
        next_attempt_at=now or utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def mint_lease(db: Session, invocation_id: uuid.UUID) -> str:
    """Create a one-use lease for an invocation attempt and return its plaintext token.

    Earlier unused leases for the same invocation are revoked, so only the newest attempt can be exchanged.
    Call inside a transaction the caller commits before the provider is called.
    """
    now = utcnow()
    db.execute(
        update(InvocationLease)
        .where(
            InvocationLease.invocation_id == invocation_id,
            InvocationLease.used_at.is_(None),
            InvocationLease.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    token = secrets.token_urlsafe(32)
    db.add(
        InvocationLease(
            invocation_id=invocation_id,
            token_hash=hash_token(token),
            expires_at=now + dt.timedelta(minutes=RUN_LEASE_MINUTES),
        )
    )
    return token


def _aware(value: dt.datetime) -> dt.datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=dt.UTC)


def retry_delay_seconds(attempts: int) -> int:
    """Exponential backoff: 10, 20, 40, ... capped at 5 minutes."""
    return min(10 * (2 ** max(attempts - 1, 0)), 300)


def claim_due(
    db: Session, *, owner: str, now: dt.datetime, lease_seconds: int = 120, limit: int = 10
) -> list[uuid.UUID]:
    """Lease up to `limit` due rows. Commits the claim. Returns outbox ids."""
    candidates = db.scalars(
        select(ExecutionOutbox)
        .where(ExecutionOutbox.status.in_(("pending", "claimed")))
        .order_by(ExecutionOutbox.next_attempt_at, ExecutionOutbox.created_at)
        .limit(limit * 5)
        .with_for_update(skip_locked=True)
    ).all()
    claimed: list[uuid.UUID] = []
    for row in candidates:
        if len(claimed) >= limit:
            break
        if row.status == "pending" and _aware(row.next_attempt_at) <= now:
            pass
        elif row.status == "claimed" and row.lease_expires_at is not None and _aware(row.lease_expires_at) <= now:
            pass  # lease expired: the previous worker died, reclaim it
        else:
            continue
        row.status = "claimed"
        row.lease_owner = owner
        row.lease_expires_at = now + dt.timedelta(seconds=lease_seconds)
        claimed.append(row.id)
    db.commit()
    return claimed


def process_once(
    session_factory: sessionmaker[Session],
    provider: ExecutionProvider,
    *,
    owner: str,
    now: dt.datetime | None = None,
    lease_seconds: int = 120,
    max_attempts: int = 5,
    limit: int = 10,
) -> int:
    """Run one pass of the outbox worker. Returns how many rows were processed."""
    now = now or utcnow()
    with session_factory() as db:
        ids = claim_due(db, owner=owner, now=now, lease_seconds=lease_seconds, limit=limit)

    processed = 0
    for outbox_id in ids:
        # Read what the provider needs and mint the run lease, then commit before calling out.
        with session_factory() as db:
            row = db.get(ExecutionOutbox, outbox_id)
            if row is None or row.status != "claimed" or row.lease_owner != owner:
                continue
            invocation_id = row.invocation_id
            dedupe_key = row.dedupe_key
            invocation = db.get(AgentInvocation, invocation_id)
            if invocation is None or invocation.status in {s.value for s in TERMINAL_INVOCATION_STATUSES}:
                # Cancelled or failed before it started: never start it now.
                row.status = "done"
                row.completed_at = utcnow()
                row.lease_owner = None
                row.lease_expires_at = None
                db.commit()
                continue
            lease_token = mint_lease(db, invocation_id)
            db.commit()

        execution_id: str | None = None
        error: str | None = None
        try:
            execution_id = provider.submit(invocation_id=invocation_id, dedupe_key=dedupe_key, lease_token=lease_token)
        except Exception as exc:  # any provider failure is retried; details are truncated and stored
            error = f"{type(exc).__name__}: {exc}"[:500]

        with session_factory() as db:
            row = db.get(ExecutionOutbox, outbox_id)
            invocation = db.get(AgentInvocation, invocation_id)
            if row is None or invocation is None:
                continue
            row.lease_owner = None
            row.lease_expires_at = None
            row.attempts += 1
            if execution_id is not None:
                # Bind the lease to the execution so an exchange must come from the run we started.
                db.execute(
                    update(InvocationLease)
                    .where(InvocationLease.token_hash == hash_token(lease_token))
                    .values(provider_execution_id=execution_id)
                )
                row.status = "done"
                row.completed_at = utcnow()
                row.last_error = None  # the invocation stays queued until the runtime exchanges its lease
            else:
                row.last_error = error
                if row.attempts >= max_attempts:
                    row.status = "dead"
                    row.completed_at = utcnow()
                    if invocation.status == InvocationStatus.QUEUED.value:
                        invocation.status = InvocationStatus.FAILED.value
                        invocation.error_code = "dispatch_failed"
                        invocation.error_message = "The agent runtime did not accept the run."
                        invocation.completed_at = utcnow()
                else:
                    row.status = "pending"
                    row.next_attempt_at = now + dt.timedelta(seconds=retry_delay_seconds(row.attempts))
            db.commit()
            processed += 1
    return processed


__all__ = [
    "RUN_LEASE_MINUTES",
    "ExecutionProvider",
    "FakeExecutionProvider",
    "ProviderError",
    "claim_due",
    "enqueue_dispatch",
    "hash_token",
    "mint_lease",
    "process_once",
    "retry_delay_seconds",
]
