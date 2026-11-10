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


class InvocationStatus(StrEnum):
    QUEUED = "queued"  # waiting for the execution worker to start the run
    ACCEPTED = "accepted"  # the run's runtime exchanged its lease and is working
    RUNNING = "running"  # the agent is producing output
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_INVOCATION_STATUSES = frozenset(
    {InvocationStatus.COMPLETED, InvocationStatus.FAILED, InvocationStatus.CANCELLED}
)


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
