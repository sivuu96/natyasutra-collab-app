"""Project-scoped agent routes: attach/detach, invocations, cancellation and approvals."""

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.deps import get_current_user, get_db
from app.models import Agent, AgentApproval, AgentInvocation, AgentSession, BridgeDevice, ProjectAgent, User
from app.models.enums import ApprovalStatus, InvocationStatus, ProjectRole
from app.realtime.bridge_registry import bridge_registry
from app.realtime.hub import hub
from app.services.agents import (
    apply_outcome,
    approval_resolved_frame,
    device_is_online,
    request_cancel,
)
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
    capabilities: list[str]
    owner_user_id: uuid.UUID
    owner_display_name: str
    online: bool


class AttachIn(BaseModel):
    agent_id: uuid.UUID


def _session_visible(user: User):  # type: ignore[no-untyped-def]
    """Channel invocations are always visible; session invocations only if the session is shared or yours."""
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
    queued_if_offline: bool
    error_code: str | None
    error_message: str | None
    created_at: Any
    completed_at: Any


class ApprovalOut(BaseModel):
    id: uuid.UUID
    invocation_id: uuid.UUID
    agent_id: uuid.UUID
    agent_handle: str
    operation: str
    tools: list[str]
    detail: str | None
    status: str
    created_at: Any


class ApprovalDecisionIn(BaseModel):
    decision: Literal["approved", "denied"]


def _req_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _is_moderator(role: str) -> bool:
    return ROLE_RANK[role] >= ROLE_RANK[ProjectRole.ADMIN.value]


def _agent_online(db: Session, agent: Agent) -> bool:
    device = db.get(BridgeDevice, agent.device_id)
    return agent.revoked_at is None and device is not None and device_is_online(device)


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
    return [
        ProjectAgentOut(
            agent_id=a.id,
            handle=a.handle,
            display_name=a.display_name,
            harness=a.harness,
            description=a.description,
            capabilities=list(a.capabilities or []),
            owner_user_id=owner.id,
            owner_display_name=owner.display_name,
            online=_agent_online(db, a),
        )
        for a, owner in rows
    ]


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
    return ProjectAgentOut(
        agent_id=agent.id,
        handle=agent.handle,
        display_name=agent.display_name,
        harness=agent.harness,
        description=agent.description,
        capabilities=list(agent.capabilities or []),
        owner_user_id=owner.id,
        owner_display_name=owner.display_name,
        online=_agent_online(db, agent),
    )


@router.delete("/projects/{project_id}/agents/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
def detach_agent(
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    _, membership = load_project_for(db, user, project_id, ProjectRole.MEMBER)
    link = db.scalar(
        select(ProjectAgent).where(ProjectAgent.project_id == project_id, ProjectAgent.agent_id == agent_id)
    )
    if link is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    agent = db.get(Agent, agent_id)
    if (agent is None or agent.owner_user_id != user.id) and not _is_moderator(membership.role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
    db.delete(link)
    record_audit(
        db,
        action="agent.detached",
        actor_user_id=user.id,
        project_id=project_id,
        target_type="agent",
        target_id=str(agent_id),
        request_id=_req_id(request),
    )
    db.commit()


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
    return [
        InvocationOut(
            id=i.id,
            agent_id=a.id,
            agent_handle=a.handle,
            channel_id=i.channel_id,
            session_id=i.session_id,
            requested_by_user_id=i.requested_by_user_id,
            status=i.status,
            queued_if_offline=i.queued_if_offline,
            error_code=i.error_code,
            error_message=i.error_message,
            created_at=i.created_at,
            completed_at=i.completed_at,
        )
        for i, a in rows
    ]


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
    return InvocationOut(
        id=invocation.id,
        agent_id=agent.id,
        agent_handle=agent.handle,
        channel_id=invocation.channel_id,
        requested_by_user_id=invocation.requested_by_user_id,
        status=invocation.status,
        queued_if_offline=invocation.queued_if_offline,
        error_code=invocation.error_code,
        error_message=invocation.error_message,
        created_at=invocation.created_at,
        completed_at=invocation.completed_at,
    )


@router.get("/projects/{project_id}/approvals", response_model=list[ApprovalOut])
def list_approvals(
    project_id: uuid.UUID,
    status_filter: str = Query(default="pending", alias="status"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ApprovalOut]:
    load_project_for(db, user, project_id)
    rows = db.execute(
        select(AgentApproval, Agent)
        .join(Agent, Agent.id == AgentApproval.agent_id)
        .join(AgentInvocation, AgentInvocation.id == AgentApproval.invocation_id)
        .outerjoin(AgentSession, AgentSession.id == AgentInvocation.session_id)
        .where(
            AgentApproval.project_id == project_id,
            AgentApproval.status == status_filter,
            _session_visible(user),
        )
        .order_by(AgentApproval.created_at.desc())
        .limit(200)
    ).all()
    return [
        ApprovalOut(
            id=ap.id,
            invocation_id=ap.invocation_id,
            agent_id=a.id,
            agent_handle=a.handle,
            operation=ap.operation,
            tools=list(ap.tools or []),
            detail=ap.detail,
            status=ap.status,
            created_at=ap.created_at,
        )
        for ap, a in rows
    ]


@router.post("/projects/{project_id}/approvals/{approval_id}", response_model=ApprovalOut)
def decide_approval(
    project_id: uuid.UUID,
    approval_id: uuid.UUID,
    payload: ApprovalDecisionIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApprovalOut:
    _, membership = load_project_for(db, user, project_id, ProjectRole.MEMBER)
    approval = db.scalar(
        select(AgentApproval).where(AgentApproval.id == approval_id, AgentApproval.project_id == project_id)
    )
    if approval is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    agent = db.get(Agent, approval.agent_id)
    assert agent is not None
    invocation_for_privacy = db.get(AgentInvocation, approval.invocation_id)
    if invocation_for_privacy is not None and invocation_for_privacy.session_id is not None:
        session = db.get(AgentSession, invocation_for_privacy.session_id)
        # A private session's approvals are visible only to its owner and the agent's owner.
        if (
            session is not None
            and session.visibility == "private"
            and session.owner_user_id != user.id
            and agent.owner_user_id != user.id
        ):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    # Approval belongs to the agent owner (who runs the machine) and to project admins.
    if agent.owner_user_id != user.id and not _is_moderator(membership.role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the agent owner can decide")
    if approval.status != ApprovalStatus.PENDING.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Approval already decided")
    invocation = db.get(AgentInvocation, approval.invocation_id)
    if invocation is None or invocation.status != InvocationStatus.AWAITING_APPROVAL.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="The request is no longer waiting for approval"
        )

    approval.status = ApprovalStatus.APPROVED.value if payload.decision == "approved" else ApprovalStatus.DENIED.value
    approval.decided_by_user_id = user.id
    approval.decided_at = utcnow()
    envelope = record_event(
        db,
        project_id=project_id,
        event_type="agent.approval.resolved",
        actor_user_id=user.id,
        payload={
            "approval_id": str(approval.id),
            "invocation_id": str(approval.invocation_id),
            "agent_handle": agent.handle,
            "decision": approval.status,
        },
    )
    record_audit(
        db,
        action="agent.approval.resolved",
        actor_user_id=user.id,
        project_id=project_id,
        target_type="agent_approval",
        target_id=str(approval.id),
        request_id=_req_id(request),
    )
    db.commit()
    db.refresh(approval)
    device = db.get(BridgeDevice, agent.device_id)
    if device is not None and device_is_online(device):
        bridge_registry.send(device.id, approval_resolved_frame(approval))
    hub.publish(project_id, envelope)
    return ApprovalOut(
        id=approval.id,
        invocation_id=approval.invocation_id,
        agent_id=agent.id,
        agent_handle=agent.handle,
        operation=approval.operation,
        tools=list(approval.tools or []),
        detail=approval.detail,
        status=approval.status,
        created_at=approval.created_at,
    )
