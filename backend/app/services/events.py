"""Persist-then-broadcast for project events.

`record_event` allocates the next per-project sequence number inside the caller's transaction.
The caller must commit, and only then call `hub.publish(...)` with the returned envelope.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models import Project, ProjectEvent

SCHEMA_VERSION = 1


def record_event(
    db: Session,
    *,
    project_id: uuid.UUID,
    event_type: str,
    actor_user_id: uuid.UUID | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    # Row lock serialises sequence allocation on PostgreSQL; SQLite serialises writers anyway.
    project = db.scalar(select(Project).where(Project.id == project_id).with_for_update())
    if project is None:
        raise ValueError("project not found for event")
    project.event_seq += 1
    event = ProjectEvent(
        project_id=project_id,
        sequence=project.event_seq,
        event_type=event_type,
        actor_user_id=actor_user_id,
        payload=payload,
        occurred_at=utcnow(),
    )
    db.add(event)
    db.flush()
    return envelope_for(event)


def envelope_for(event: ProjectEvent) -> dict[str, Any]:
    payload = event.payload or {}
    return {
        "event_id": str(event.id),
        "schema_version": SCHEMA_VERSION,
        "event_type": event.event_type,
        "project_id": str(event.project_id),
        "sequence": event.sequence,
        "occurred_at": event.occurred_at.isoformat(),
        "actor_user_id": str(event.actor_user_id) if event.actor_user_id else None,
        # Visibility is copied to the top level so every delivery path can filter the same way.
        "visibility": payload.get("visibility"),
        "owner_user_id": payload.get("owner_user_id"),
        "session_id": payload.get("session_id"),
        "payload": payload,
    }
