"""Project-scoped agent routes: attach, runs (invocations) and cancellation."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models import Agent, AgentInvocation, AgentSession, ProjectAgent, User
from app.models.enums import ProjectRole
from app.realtime.hub import hub
from app.services.agents import apply_outcome, request_cancel
from app.services.audit import record_audit
from app.services.authz import ROLE_RANK, load_project_for
from app.services.events import record_event

router = APIRouter(tags=["agents"])


class ProjectAgentOut(BaseModel):
    agent_id: uuid.UUID
    handle: str
    display_name: str
    harness: str
    description: str | None
    owner_user_id: uuid.UUID
    owner_display_name: str


class AttachIn(BaseModel):
    agent_id: uuid.UUID


def _session_visible(user: User):  # type: ignore[no-untyped-def]
    """Channel runs are always visible; session runs only if the session is shared or yours."""
    return or_(
        AgentInvocation.session_id.is_(None),
        AgentSession.visibility == "project",
        AgentSession.owner_user_id == user.id,
    )


class InvocationOut(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    agent_handle: str
    channel_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None
    requested_by_user_id: uuid.UUID | None
    status: str
    error_code: str | None
    error_message: str | None
    created_at: Any
    completed_at: Any


def _req_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _is_moderator(role: str) -> bool:
    return ROLE_RANK[role] >= ROLE_RANK[ProjectRole.ADMIN.value]


def _agent_out(agent: Agent, owner: User) -> ProjectAgentOut:
    return ProjectAgentOut(
        agent_id=agent.id,
        handle=agent.handle,
        display_name=agent.display_name,
        harness=agent.harness,
        description=agent.description,
        owner_user_id=owner.id,
        owner_display_name=owner.display_name,
    )


def _invocation_out(invocation: AgentInvocation, agent: Agent) -> InvocationOut:
    return InvocationOut(
        id=invocation.id,
        agent_id=agent.id,
        agent_handle=agent.handle,
        channel_id=invocation.channel_id,
        session_id=invocation.session_id,
        requested_by_user_id=invocation.requested_by_user_id,
        status=invocation.status,
        error_code=invocation.error_code,
        error_message=invocation.error_message,
        created_at=invocation.created_at,
        completed_at=invocation.completed_at,
    )


class MyAgentOut(BaseModel):
    id: uuid.UUID
    handle: str
    display_name: str
    harness: str
    description: str | None
    repository_full_name: str | None
    default_ref: str | None
    revoked: bool


@router.get("/me/agents", response_model=list[MyAgentOut])
def my_agents(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[MyAgentOut]:
    """Agents you own. Attach one to a project to offer it there."""
    rows = db.scalars(select(Agent).where(Agent.owner_user_id == user.id).order_by(Agent.handle)).all()
    return [
        MyAgentOut(
            id=a.id,
            handle=a.handle,
            display_name=a.display_name,
            harness=a.harness,
            description=a.description,
            repository_full_name=a.repository_full_name,
            default_ref=a.default_ref,
            revoked=a.revoked_at is not None,
        )
        for a in rows
    ]


@router.get("/projects/{project_id}/agents", response_model=list[ProjectAgentOut])
def list_project_agents(
    project_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[ProjectAgentOut]:
    load_project_for(db, user, project_id)
    rows = db.execute(
        select(Agent, User)
        .join(ProjectAgent, ProjectAgent.agent_id == Agent.id)
        .join(User, User.id == Agent.owner_user_id)
        .where(ProjectAgent.project_id == project_id)
        .order_by(Agent.handle)
    ).all()
    return [_agent_out(a, owner) for a, owner in rows]


@router.post("/projects/{project_id}/agents", response_model=ProjectAgentOut, status_code=status.HTTP_201_CREATED)
def attach_agent(
    project_id: uuid.UUID,
    payload: AttachIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectAgentOut:
    _, membership = load_project_for(db, user, project_id, ProjectRole.MEMBER)
    agent = db.get(Agent, payload.agent_id)
    if agent is None or agent.revoked_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    # Only the owner can offer their agent to a project; admins can attach any agent they can see.
    if agent.owner_user_id != user.id and not _is_moderator(membership.role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the agent owner can attach it")
    if (
        db.scalar(
            select(ProjectAgent.id).where(ProjectAgent.project_id == project_id, ProjectAgent.agent_id == agent.id)
        )
        is None
    ):
        db.add(ProjectAgent(project_id=project_id, agent_id=agent.id, added_by_user_id=user.id))
        db.flush()
        envelope = record_event(
            db,
            project_id=project_id,
            event_type="agent.attached",
            actor_user_id=user.id,
            payload={"agent_handle": agent.handle},
        )
        record_audit(
            db,
            action="agent.attached",
            actor_user_id=user.id,
            project_id=project_id,
            target_type="agent",
            target_id=str(agent.id),
            request_id=_req_id(request),
        )
        db.commit()
        hub.publish(project_id, envelope)
    owner = db.get(User, agent.owner_user_id)
    assert owner is not None
    return _agent_out(agent, owner)


@router.get("/projects/{project_id}/invocations", response_model=list[InvocationOut])
def list_invocations(
    project_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[InvocationOut]:
    load_project_for(db, user, project_id)
    rows = db.execute(
        select(AgentInvocation, Agent)
        .join(Agent, Agent.id == AgentInvocation.agent_id)
        .outerjoin(AgentSession, AgentSession.id == AgentInvocation.session_id)
        .where(AgentInvocation.project_id == project_id, _session_visible(user))
        .order_by(AgentInvocation.created_at.desc())
        .limit(limit)
    ).all()
    return [_invocation_out(i, a) for i, a in rows]


@router.post("/projects/{project_id}/invocations/{invocation_id}/cancel", response_model=InvocationOut)
def cancel_invocation(
    project_id: uuid.UUID,
    invocation_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InvocationOut:
    _, membership = load_project_for(db, user, project_id, ProjectRole.MEMBER)
    invocation = db.scalar(
        select(AgentInvocation).where(AgentInvocation.id == invocation_id, AgentInvocation.project_id == project_id)
    )
    if invocation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    agent = db.get(Agent, invocation.agent_id)
    allowed = (
        invocation.requested_by_user_id == user.id
        or _is_moderator(membership.role)
        or (agent is not None and agent.owner_user_id == user.id)
    )
    if not allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
    outcome = request_cancel(db, invocation, user)
    db.commit()
    db.refresh(invocation)
    apply_outcome(outcome)
    assert agent is not None
    return _invocation_out(invocation, agent)
