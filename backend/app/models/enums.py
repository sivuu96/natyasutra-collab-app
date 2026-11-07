"""Domain enums. Stored as strings with CHECK constraints (portable across SQLite and PostgreSQL)."""

from enum import StrEnum


class ProjectRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class TeamRole(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class TaskStatus(StrEnum):
    BACKLOG = "backlog"
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    BLOCKED = "blocked"
    DONE = "done"
    CANCELLED = "cancelled"


class TaskPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class DecisionStatus(StrEnum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class HarnessType(StrEnum):
    CLAUDE_CODE = "claude_code"
    CODEX = "codex"


class InvocationStatus(StrEnum):
    QUEUED = "queued"  # stored; bridge offline and sender chose to queue
    DELIVERED = "delivered"  # bridge received the frame over its socket
    ACCEPTED = "accepted"  # bridge validated the request and will run it
    RUNNING = "running"  # harness is producing output
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"  # bridge refused (policy, unknown agent, bad path)


TERMINAL_INVOCATION_STATUSES = frozenset(
    {InvocationStatus.COMPLETED, InvocationStatus.FAILED, InvocationStatus.CANCELLED, InvocationStatus.REJECTED}
)


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


class SessionVisibility(StrEnum):
    PRIVATE = "private"  # owner only
    PROJECT = "project"  # every project member can read the transcript and live output


class SessionStatus(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"


class AuditOutcome(StrEnum):
    SUCCESS = "success"
    DENIED = "denied"
    FAILURE = "failure"


def check_in(column: str, enum_cls: type[StrEnum]) -> str:
    """Build a SQL CHECK expression restricting a column to an enum's values."""
    values = ", ".join(f"'{member.value}'" for member in enum_cls)
    return f"{column} IN ({values})"
