"""Dedicated agent sessions: private or shared conversations with one attached agent.

Visibility rules (enforced here, and again on every realtime delivery):
- private (default): only the session owner can see the session, its transcript, or its live output.
  Everyone else gets 404, so the session's existence is not revealed.
- project: every project member can read the transcript and live output. Only the owner can send turns.
"""

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.schemas import RunFileOut
from app.db.base import utcnow
from app.deps import get_current_user, get_db
from app.models import Agent, AgentInvocation, AgentSession, AgentSessionMessage, ProjectAgent, User
from app.models.enums import TERMINAL_INVOCATION_STATUSES, ProjectRole, SessionStatus, SessionVisibility
from app.models.sessions import SessionWorkspaceTurn
from app.realtime.hub import hub
from app.services.agents import Outcome, apply_outcome, create_session_turn
from app.services.audit import record_audit
from app.services.authz import ROLE_RANK, get_membership, load_project_for
from app.services.events import record_event
from app.services.files import files_by_invocation

router = APIRouter(tags=["sessions"])

TERMINAL_VALUES = {s.value for s in TERMINAL_INVOCATION_STATUSES}


class SessionIn(BaseModel):
    agent_id: uuid.UUID
    title: str = Field(min_length=1, max_length=120)
    visibility: Literal["private", "project"] = "private"


class TurnIn(BaseModel):
    body: str = Field(min_length=1, max_length=10000)


class VisibilityIn(BaseModel):
    visibility: Literal["private", "project"]


class SessionOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    agent_id: uuid.UUID
    agent_handle: str
    title: str
    visibility: str
    status: str
    owner_user_id: uuid.UUID
    owner_display_name: str
    is_owner: bool
    can_send: bool
    can_delete: bool
    last_activity_at: Any
    created_at: Any


class SessionMessageOut(BaseModel):
    id: uuid.UUID
    role: str
    author_display_name: str | None
    agent_handle: str | None
    body: str
    invocation_id: uuid.UUID | None
    created_at: Any
    files: list[RunFileOut] = []


class TurnOut(BaseModel):
    invocation_id: uuid.UUID
    status: str
    error_code: str | None


class WorkspaceOut(BaseModel):
    """The working folder after the latest saved turn: the tree, what changed in that turn, and the diff."""

    invocation_id: uuid.UUID | None
    saved_at: Any
    file_count: int
    total_bytes: int
    files: list[dict[str, Any]]
    changes: list[dict[str, Any]]
    diff: str | None
    diff_truncated: bool


def _req_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _owner_name(db: Session, session: AgentSession) -> str:
    owner = db.get(User, session.owner_user_id)
    return owner.display_name if owner else "Former member"


def _session_out(db: Session, session: AgentSession, user: User) -> SessionOut:
    agent = db.get(Agent, session.agent_id)
    return SessionOut(
        id=session.id,
        project_id=session.project_id,
        agent_id=session.agent_id,
        agent_handle=agent.handle if agent else "",
        title=session.title,
        visibility=session.visibility,
        status=session.status,
        owner_user_id=session.owner_user_id,
        owner_display_name=_owner_name(db, session),
        is_owner=session.owner_user_id == user.id,
        can_send=_can_send(db, user, session),
        can_delete=_can_delete(db, user, session),
        last_activity_at=session.last_activity_at,
        created_at=session.created_at,
    )


def _can_see(session: AgentSession, user: User) -> bool:
    return session.owner_user_id == user.id or session.visibility == SessionVisibility.PROJECT.value


def _can_delete(db: Session, user: User, session: AgentSession) -> bool:
    """The session's creator, and any project owner or admin, may delete a session."""
    if session.owner_user_id == user.id:
        return True
    membership = get_membership(db, user, session.project_id)
    return membership is not None and membership.role in (ProjectRole.OWNER.value, ProjectRole.ADMIN.value)


def _visible_session(db: Session, user: User, project_id: uuid.UUID, session_id: uuid.UUID) -> AgentSession:
    session = db.scalar(
        select(AgentSession).where(
            AgentSession.id == session_id,
            AgentSession.project_id == project_id,
            AgentSession.deleted_at.is_(None),
        )
    )
    if session is None or not _can_see(session, user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return session


def _can_send(db: Session, user: User, session: AgentSession) -> bool:
    """Who may send turns: the session's owner, and, for a shared session, the project's owners."""
    if session.owner_user_id == user.id:
        return True
    if session.visibility != SessionVisibility.PROJECT.value:
        return False
    membership = get_membership(db, user, session.project_id)
    return membership is not None and membership.role == ProjectRole.OWNER.value


def _owned_session(db: Session, user: User, project_id: uuid.UUID, session_id: uuid.UUID) -> AgentSession:
    session = _visible_session(db, user, project_id, session_id)
    if session.owner_user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the session owner can do that")
    return session


@router.post("/projects/{project_id}/sessions", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
def create_session(
    project_id: uuid.UUID,
    payload: SessionIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SessionOut:
    load_project_for(db, user, project_id, ProjectRole.MEMBER)
    attached = db.scalar(
        select(ProjectAgent.id).where(ProjectAgent.project_id == project_id, ProjectAgent.agent_id == payload.agent_id)
    )
    agent = db.get(Agent, payload.agent_id)
    if attached is None or agent is None or agent.revoked_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent is not available in this project")
    session = AgentSession(
        project_id=project_id,
        agent_id=agent.id,
        owner_user_id=user.id,
        title=payload.title.strip(),
        visibility=payload.visibility,
        status=SessionStatus.ACTIVE.value,
        last_activity_at=utcnow(),
    )
    db.add(session)
    db.flush()
    envelope = record_event(
        db,
        project_id=project_id,
        event_type="session.created",
        actor_user_id=user.id,
        payload={
            "session_id": str(session.id),
            "agent_handle": agent.handle,
            "visibility": session.visibility,
            "owner_user_id": str(user.id),
        },
    )
    record_audit(
        db,
        action="session.created",
        actor_user_id=user.id,
        project_id=project_id,
        target_type="agent_session",
        target_id=str(session.id),
        request_id=_req_id(request),
    )
    db.commit()
    db.refresh(session)
    hub.publish(project_id, envelope)
    return _session_out(db, session, user)


@router.get("/projects/{project_id}/sessions", response_model=list[SessionOut])
def list_sessions(
    project_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[SessionOut]:
    load_project_for(db, user, project_id)
    rows = db.scalars(
        select(AgentSession)
        .where(
            AgentSession.project_id == project_id,
            AgentSession.deleted_at.is_(None),
            or_(AgentSession.owner_user_id == user.id, AgentSession.visibility == SessionVisibility.PROJECT.value),
        )
        .order_by(AgentSession.created_at.desc())
        .limit(200)
    ).all()
    return [_session_out(db, s, user) for s in rows]


@router.get("/projects/{project_id}/sessions/{session_id}", response_model=SessionOut)
def get_session(
    project_id: uuid.UUID, session_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> SessionOut:
    load_project_for(db, user, project_id)
    return _session_out(db, _visible_session(db, user, project_id, session_id), user)


@router.get("/projects/{project_id}/sessions/{session_id}/workspace", response_model=WorkspaceOut | None)
def session_workspace(
    project_id: uuid.UUID,
    session_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WorkspaceOut | None:
    """The latest saved working folder of a session. Visible to everyone who can read the session."""
    load_project_for(db, user, project_id)
    _visible_session(db, user, project_id, session_id)
    row = db.scalars(
        select(SessionWorkspaceTurn)
        .where(SessionWorkspaceTurn.session_id == session_id)
        .order_by(SessionWorkspaceTurn.created_at.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    return WorkspaceOut(
        invocation_id=row.invocation_id,
        saved_at=row.created_at,
        file_count=row.file_count,
        total_bytes=row.total_bytes,
        files=row.files,
        changes=row.changes,
        diff=row.diff,
        diff_truncated=row.diff_truncated,
    )


@router.get("/projects/{project_id}/sessions/{session_id}/messages", response_model=list[SessionMessageOut])
def session_messages(
    project_id: uuid.UUID,
    session_id: uuid.UUID,
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[SessionMessageOut]:
    load_project_for(db, user, project_id)
    _visible_session(db, user, project_id, session_id)
    rows = list(
        db.scalars(
            select(AgentSessionMessage)
            .where(AgentSessionMessage.session_id == session_id)
            .order_by(AgentSessionMessage.created_at.desc())
            .limit(limit)
        ).all()
    )
    rows.reverse()
    files = files_by_invocation(db, [m.invocation_id for m in rows if m.invocation_id])
    out: list[SessionMessageOut] = []
    for m in rows:
        author = db.get(User, m.author_user_id) if m.author_user_id else None
        agent = db.get(Agent, m.agent_id) if m.agent_id else None
        out.append(
            SessionMessageOut(
                id=m.id,
                role=m.role,
                author_display_name=author.display_name if author else None,
                agent_handle=agent.handle if agent else None,
                body=m.body,
                invocation_id=m.invocation_id,
                created_at=m.created_at,
                files=[
                    RunFileOut(id=f.id, name=f.name, size_bytes=f.size_bytes) for f in files.get(m.invocation_id, ())
                ]
                if m.invocation_id
                else [],
            )
        )
    return out


@router.post(
    "/projects/{project_id}/sessions/{session_id}/turns", response_model=TurnOut, status_code=status.HTTP_201_CREATED
)
def post_turn(
    project_id: uuid.UUID,
    session_id: uuid.UUID,
    payload: TurnIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TurnOut:
    load_project_for(db, user, project_id, ProjectRole.MEMBER)
    session = _visible_session(db, user, project_id, session_id)
    if not _can_send(db, user, session):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the session owner or a project owner can send messages here",
        )
    if session.status != SessionStatus.ACTIVE.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This session is closed")
    busy = db.scalar(
        select(AgentInvocation.id).where(
            AgentInvocation.session_id == session.id,
            AgentInvocation.status.notin_(list(TERMINAL_VALUES)),
        )
    )
    if busy is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A turn is already in progress")

    db.add(
        AgentSessionMessage(
            session_id=session.id, project_id=project_id, role="user", author_user_id=user.id, body=payload.body
        )
    )
    outcome = Outcome()
    try:
        invocation_id = create_session_turn(db, session=session, body=payload.body, requester=user, outcome=outcome)
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    record_audit(
        db,
        action="session.turn_requested",
        actor_user_id=user.id,
        project_id=project_id,
        target_type="agent_session",
        target_id=str(session.id),
        request_id=_req_id(request),
    )
    db.commit()
    apply_outcome(outcome)  # the run is queued in the same transaction; the worker starts it
    invocation = db.get(AgentInvocation, invocation_id)
    assert invocation is not None
    return TurnOut(invocation_id=invocation.id, status=invocation.status, error_code=invocation.error_code)


@router.post("/projects/{project_id}/sessions/{session_id}/visibility", response_model=SessionOut)
def set_visibility(
    project_id: uuid.UUID,
    session_id: uuid.UUID,
    payload: VisibilityIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SessionOut:
    load_project_for(db, user, project_id, ProjectRole.MEMBER)
    session = _owned_session(db, user, project_id, session_id)
    session.visibility = payload.visibility
    envelope = record_event(
        db,
        project_id=project_id,
        event_type="session.visibility_changed",
        actor_user_id=user.id,
        payload={
            "session_id": str(session.id),
            "visibility": session.visibility,
            "owner_user_id": str(session.owner_user_id),
        },
    )
    record_audit(
        db,
        action="session.visibility_changed",
        actor_user_id=user.id,
        project_id=project_id,
        target_type="agent_session",
        target_id=str(session.id),
        request_id=_req_id(request),
    )
    db.commit()
    db.refresh(session)
    hub.publish(project_id, envelope)
    return _session_out(db, session, user)


@router.delete("/projects/{project_id}/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(
    project_id: uuid.UUID,
    session_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Delete a session. Its creator, a project owner or a project admin may do this, even for a private session."""
    load_project_for(db, user, project_id, ProjectRole.MEMBER)
    session = db.scalar(
        select(AgentSession).where(
            AgentSession.id == session_id,
            AgentSession.project_id == project_id,
            AgentSession.deleted_at.is_(None),
        )
    )
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if not _can_delete(db, user, session):
        if not _can_see(session, user):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the session's creator, a project owner or an admin can delete it",
        )
    busy = db.scalar(
        select(AgentInvocation.id).where(
            AgentInvocation.session_id == session.id,
            AgentInvocation.status.notin_(list(TERMINAL_VALUES)),
        )
    )
    if busy is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Wait for the current turn to finish")

    session.deleted_at = utcnow()
    envelope = record_event(
        db,
        project_id=project_id,
        event_type="session.deleted",
        actor_user_id=user.id,
        payload={
            "session_id": str(session.id),
            "visibility": session.visibility,
            "owner_user_id": str(session.owner_user_id),
        },
    )
    record_audit(
        db,
        action="session.deleted",
        actor_user_id=user.id,
        project_id=project_id,
        target_type="agent_session",
        target_id=str(session.id),
        request_id=_req_id(request),
    )
    db.commit()
    hub.publish(project_id, envelope)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/projects/{project_id}/sessions/{session_id}/close", response_model=SessionOut)
def close_session(
    project_id: uuid.UUID,
    session_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SessionOut:
    _, membership = load_project_for(db, user, project_id, ProjectRole.MEMBER)
    session = _visible_session(db, user, project_id, session_id)
    if session.owner_user_id != user.id and ROLE_RANK[membership.role] < ROLE_RANK[ProjectRole.ADMIN.value]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the owner or an admin can close it")
    session.status = SessionStatus.CLOSED.value
    record_audit(
        db,
        action="session.closed",
        actor_user_id=user.id,
        project_id=project_id,
        target_type="agent_session",
        target_id=str(session.id),
        request_id=_req_id(request),
    )
    db.commit()
    db.refresh(session)
    return _session_out(db, session, user)
