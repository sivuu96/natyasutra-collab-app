"""Append-only audit writes. Never record message bodies or credentials."""

import uuid

from sqlalchemy.orm import Session

from app.models import AuditEvent
from app.models.enums import AuditOutcome


def record_audit(
    db: Session,
    *,
    action: str,
    actor_user_id: uuid.UUID | None,
    project_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    outcome: AuditOutcome = AuditOutcome.SUCCESS,
    request_id: str | None = None,
) -> None:
    db.add(
        AuditEvent(
            action=action,
            actor_user_id=actor_user_id,
            project_id=project_id,
            target_type=target_type,
            target_id=target_id,
            outcome=outcome.value,
            request_id=request_id,
        )
    )
