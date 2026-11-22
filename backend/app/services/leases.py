"""Exchange a one-use run lease for the run's context and a short-lived runtime token.

The runtime calls the exchange once at startup. Every check below is enforced on the server, and the lease is
burned only after all of them pass:
- the token matches a stored hash, is not revoked or expired, and has not been used;
- the invocation is still active;
- the execution name the runtime reports is the one the provider recorded for this lease.

A mismatched execution name is rejected without burning the lease, so a legitimate runtime can retry.
"""

import datetime as dt
import uuid
from dataclasses import dataclass

import jwt
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models.agents import Agent, AgentInvocation
from app.models.enums import TERMINAL_INVOCATION_STATUSES, InvocationStatus
from app.models.execution import InvocationLease
from app.services.execution import hash_token
from app.services.transcript import session_prompt

RUNTIME_TOKEN_MINUTES = 15
RUNTIME_AUDIENCE = "collab-runtime"
RUNTIME_ISSUER = "collab-hub"


class LeaseError(Exception):
    def __init__(self, status_code: int, code: str) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code


# Branches a run may never push to, whatever the agent's flag says.
PROTECTED_REFS = frozenset({"main", "master"})
PUSH_TRIGGER = "#commit-push"  # a message containing this asks the agent to push its branch


def write_allowed(ref: str) -> bool:
    """True when a run on this branch may commit and push. Protected branches are never writable."""
    return ref not in PROTECTED_REFS


@dataclass(frozen=True)
class RunContext:
    invocation_id: uuid.UUID
    prompt: str
    agent_handle: str
    harness: str
    runtime_token: str
    runtime_expires_in: int
    repository: str | None = None
    ref: str | None = None
    can_write: bool = False
    # True when this turn's own message asks for a push. Earlier turns in a session are not checked.
    push_requested: bool = False
    # This turn's own message, without the session history, and the session it belongs to (None for a channel mention).
    message: str = ""
    session_id: str | None = None


def _aware(value: dt.datetime) -> dt.datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=dt.UTC)


def issue_runtime_token(secret: str, invocation_id: uuid.UUID, now: dt.datetime) -> tuple[str, int]:
    expires = now + dt.timedelta(minutes=RUNTIME_TOKEN_MINUTES)
    token = jwt.encode(
        {
            "sub": str(invocation_id),
            "aud": RUNTIME_AUDIENCE,
            "iss": RUNTIME_ISSUER,
            "iat": now,
            "exp": expires,
        },
        secret,
        algorithm="HS256",
    )
    return token, RUNTIME_TOKEN_MINUTES * 60


def verify_runtime_token(secret: str, token: str) -> uuid.UUID:
    """Return the invocation a runtime token was issued for. Raises jwt.PyJWTError if invalid or expired."""
    claims = jwt.decode(
        token,
        secret,
        algorithms=["HS256"],
        audience=RUNTIME_AUDIENCE,
        issuer=RUNTIME_ISSUER,
        options={"require": ["exp", "aud", "iss", "sub"]},
    )
    return uuid.UUID(claims["sub"])


def exchange_lease(
    db: Session,
    *,
    lease_token: str,
    execution_name: str,
    runtime_secret: str,
    allowed_repos: frozenset[str] = frozenset(),
) -> RunContext:
    """Check the lease and return the run context. Commits the exchange. Raises LeaseError on refusal."""
    now = utcnow()
    lease = db.scalar(select(InvocationLease).where(InvocationLease.token_hash == hash_token(lease_token)))
    if lease is None:
        raise LeaseError(401, "invalid_lease")
    if lease.revoked_at is not None:
        raise LeaseError(401, "lease_revoked")
    if _aware(lease.expires_at) <= now:
        raise LeaseError(401, "lease_expired")
    if lease.used_at is not None:
        raise LeaseError(409, "lease_used")

    invocation = db.get(AgentInvocation, lease.invocation_id)
    if invocation is None or invocation.status in {s.value for s in TERMINAL_INVOCATION_STATUSES}:
        raise LeaseError(409, "run_not_active")
    if lease.provider_execution_id is None:
        raise LeaseError(409, "execution_not_bound")  # the worker has not recorded the execution yet; retry
    if execution_name != lease.provider_execution_id:
        raise LeaseError(409, "execution_mismatch")
    agent = db.get(Agent, invocation.agent_id)
    if agent is None:
        raise LeaseError(409, "run_not_active")
    # Refuse before the lease is used, so a misconfiguration does not burn the run's only credential.
    if agent.repository_full_name and agent.repository_full_name not in allowed_repos:
        raise LeaseError(409, "repository_not_allowed")

    burned = db.execute(
        update(InvocationLease)
        .where(InvocationLease.id == lease.id, InvocationLease.used_at.is_(None))
        .values(used_at=now)
    )
    if burned.rowcount != 1:  # another exchange won the race
        db.rollback()
        raise LeaseError(409, "lease_used")

    if invocation.status == InvocationStatus.QUEUED.value:
        invocation.status = InvocationStatus.ACCEPTED.value
    invocation.last_activity_at = now

    runtime_token, expires_in = issue_runtime_token(runtime_secret, invocation.id, now)
    context = RunContext(
        invocation_id=invocation.id,
        prompt=session_prompt(db, invocation),
        agent_handle=agent.handle,
        harness=agent.harness,
        runtime_token=runtime_token,
        runtime_expires_in=expires_in,
        repository=agent.repository_full_name,
        ref=agent.default_ref or ("main" if agent.repository_full_name else None),
        can_write=agent.can_write and write_allowed(agent.default_ref or "main"),
        push_requested=PUSH_TRIGGER in invocation.prompt,
        message=invocation.prompt,
        session_id=str(invocation.session_id) if invocation.session_id else None,
    )
    db.commit()
    return context


__all__ = ["LeaseError", "RunContext", "exchange_lease", "issue_runtime_token"]
