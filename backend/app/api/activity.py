"""Activity feed, audit log and project-scoped search."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models import AuditEvent, Decision, Message, ProjectEvent, Task, User
from app.models.enums import ProjectRole
from app.services.authz import load_project_for

router = APIRouter(tags=["activity"])


def _like(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _fts(db: Session, column: Any, query: str) -> Any:
    """PostgreSQL full-text match; LIKE fallback on other dialects (used by SQLite tests)."""
    if db.get_bind().dialect.name == "postgresql":
        return func.to_tsvector("simple", column).op("@@")(func.websearch_to_tsquery("simple", query))
    return column.ilike(_like(query), escape="\\")


@router.get("/projects/{project_id}/activity")
def activity_feed(
    project_id: uuid.UUID,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """Persisted project events in sequence order. Clients resume from the last sequence they saw."""
    load_project_for(db, user, project_id)
    rows = db.scalars(
        select(ProjectEvent)
        .where(ProjectEvent.project_id == project_id, ProjectEvent.sequence > after)
        .order_by(ProjectEvent.sequence)
        .limit(limit)
    ).all()
    # Private agent-session events are visible only to that session's owner.
    visible = [
        e
        for e in rows
        if (e.payload or {}).get("visibility") != "private" or (e.payload or {}).get("owner_user_id") == str(user.id)
    ]
    return [
        {
            "event_id": str(e.id),
            "event_type": e.event_type,
            "sequence": e.sequence,
            "occurred_at": e.occurred_at.isoformat(),
            "actor_user_id": str(e.actor_user_id) if e.actor_user_id else None,
            "payload": e.payload,
        }
        for e in visible
    ]


@router.get("/projects/{project_id}/audit")
def audit_log(
    project_id: uuid.UUID,
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    load_project_for(db, user, project_id, ProjectRole.ADMIN)
    rows = db.scalars(
        select(AuditEvent)
        .where(AuditEvent.project_id == project_id)
        .order_by(AuditEvent.occurred_at.desc())
        .limit(limit)
    ).all()
    return [
        {
            "id": str(a.id),
            "occurred_at": a.occurred_at.isoformat(),
            "action": a.action,
            "actor_user_id": str(a.actor_user_id) if a.actor_user_id else None,
            "target_type": a.target_type,
            "target_id": a.target_id,
            "outcome": a.outcome,
            "request_id": a.request_id,
        }
        for a in rows
    ]


@router.get("/projects/{project_id}/search")
def search(
    project_id: uuid.UUID,
    q: str = Query(min_length=2, max_length=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, list[dict[str, Any]]]:
    load_project_for(db, user, project_id)
    term = q.strip()

    messages_query: Select[Any] = (
        select(Message)
        .where(Message.project_id == project_id, Message.deleted_at.is_(None), _fts(db, Message.body, term))
        .order_by(Message.created_at.desc())
        .limit(25)
    )
    tasks_query: Select[Any] = (
        select(Task)
        .where(
            Task.project_id == project_id,
            or_(_fts(db, Task.title, term), _fts(db, func.coalesce(Task.description, ""), term)),
        )
        .order_by(Task.updated_at.desc())
        .limit(25)
    )
    decisions_query: Select[Any] = (
        select(Decision)
        .where(
            Decision.project_id == project_id,
            or_(_fts(db, Decision.title, term), _fts(db, Decision.statement, term)),
        )
        .order_by(Decision.updated_at.desc())
        .limit(25)
    )

    return {
        "messages": [
            {"id": str(m.id), "channel_id": str(m.channel_id), "snippet": m.body[:200]}
            for m in db.scalars(messages_query).all()
        ],
        "tasks": [{"id": str(t.id), "title": t.title, "status": t.status} for t in db.scalars(tasks_query).all()],
        "decisions": [
            {"id": str(d.id), "title": d.title, "status": d.status} for d in db.scalars(decisions_query).all()
        ],
    }
