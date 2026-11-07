"""Tasks and decisions. Writes emit persisted project events and audit records."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import (
    DecisionIn,
    DecisionOut,
    DecisionPatch,
    DecisionRevisionOut,
    TaskIn,
    TaskOut,
    TaskPatch,
)
from app.db.base import utcnow
from app.deps import get_current_user, get_db
from app.models import Decision, DecisionRevision, ProjectMembership, Task, User
from app.models.enums import (
    AuditOutcome,
    DecisionStatus,
    ProjectRole,
    TaskPriority,
    TaskStatus,
)
from app.realtime.hub import hub
from app.services.audit import record_audit
from app.services.authz import ROLE_RANK, load_project_for
from app.services.events import record_event

router = APIRouter(tags=["work"])

VALID_TASK_STATUS = {s.value for s in TaskStatus}
VALID_TASK_PRIORITY = {p.value for p in TaskPriority}
VALID_DECISION_STATUS = {s.value for s in DecisionStatus}
DECISION_RECORD_FIELDS = ("title", "statement", "context", "options", "rationale", "consequences", "status")


def _req_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _require_project_member_user(db: Session, project_id: uuid.UUID, user_id: uuid.UUID | None) -> None:
    if user_id is None:
        return
    is_member = (
        db.scalar(
            select(ProjectMembership.id).where(
                ProjectMembership.project_id == project_id, ProjectMembership.user_id == user_id
            )
        )
        is not None
    )
    if not is_member:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Assignee must be a project member"
        )


def _task_in_project(db: Session, project_id: uuid.UUID, task_id: uuid.UUID) -> Task:
    task = db.scalar(select(Task).where(Task.id == task_id, Task.project_id == project_id))
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return task


def _decision_in_project(db: Session, project_id: uuid.UUID, decision_id: uuid.UUID) -> Decision:
    decision = db.scalar(select(Decision).where(Decision.id == decision_id, Decision.project_id == project_id))
    if decision is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return decision


def _snapshot(decision: Decision) -> dict[str, Any]:
    return {field: getattr(decision, field) for field in DECISION_RECORD_FIELDS}


# ---- tasks ------------------------------------------------------------------------------------


@router.get("/projects/{project_id}/tasks", response_model=list[TaskOut])
def list_tasks(
    project_id: uuid.UUID,
    status_filter: str | None = Query(default=None, alias="status"),
    assignee: uuid.UUID | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Task]:
    load_project_for(db, user, project_id)
    query = select(Task).where(Task.project_id == project_id)
    if status_filter:
        query = query.where(Task.status == status_filter)
    if assignee:
        query = query.where(Task.assignee_user_id == assignee)
    return list(db.scalars(query.order_by(Task.created_at.desc()).limit(500)).all())


@router.post("/projects/{project_id}/tasks", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
def create_task(
    project_id: uuid.UUID,
    payload: TaskIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Task:
    load_project_for(db, user, project_id, ProjectRole.MEMBER)
    if payload.status not in VALID_TASK_STATUS or payload.priority not in VALID_TASK_PRIORITY:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid status or priority")
    _require_project_member_user(db, project_id, payload.assignee_user_id)
    task = Task(project_id=project_id, creator_user_id=user.id, **payload.model_dump())
    db.add(task)
    db.flush()
    envelope = record_event(
        db,
        project_id=project_id,
        event_type="task.created",
        actor_user_id=user.id,
        payload={"task_id": str(task.id), "title": task.title, "status": task.status},
    )
    record_audit(
        db,
        action="task.created",
        actor_user_id=user.id,
        project_id=project_id,
        target_type="task",
        target_id=str(task.id),
        request_id=_req_id(request),
    )
    db.commit()
    db.refresh(task)
    hub.publish(project_id, envelope)
    return task


@router.get("/projects/{project_id}/tasks/{task_id}", response_model=TaskOut)
def get_task(
    project_id: uuid.UUID, task_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> Task:
    load_project_for(db, user, project_id)
    return _task_in_project(db, project_id, task_id)


@router.patch("/projects/{project_id}/tasks/{task_id}", response_model=TaskOut)
def update_task(
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    payload: TaskPatch,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Task:
    load_project_for(db, user, project_id, ProjectRole.MEMBER)
    task = _task_in_project(db, project_id, task_id)
    changes = payload.model_dump(exclude_unset=True)
    if "status" in changes and changes["status"] not in VALID_TASK_STATUS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid status")
    if "priority" in changes and changes["priority"] not in VALID_TASK_PRIORITY:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid priority")
    if "assignee_user_id" in changes:
        _require_project_member_user(db, project_id, changes["assignee_user_id"])
    for field, value in changes.items():
        if field == "title" and value is None:
            continue
        setattr(task, field, value)
    task.updated_at = utcnow()
    envelope = record_event(
        db,
        project_id=project_id,
        event_type="task.updated",
        actor_user_id=user.id,
        payload={"task_id": str(task.id), "changed": sorted(changes.keys()), "status": task.status},
    )
    record_audit(
        db,
        action="task.updated",
        actor_user_id=user.id,
        project_id=project_id,
        target_type="task",
        target_id=str(task.id),
        request_id=_req_id(request),
    )
    db.commit()
    db.refresh(task)
    hub.publish(project_id, envelope)
    return task


# ---- decisions --------------------------------------------------------------------------------


@router.get("/projects/{project_id}/decisions", response_model=list[DecisionOut])
def list_decisions(
    project_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[Decision]:
    load_project_for(db, user, project_id)
    return list(
        db.scalars(
            select(Decision).where(Decision.project_id == project_id).order_by(Decision.created_at.desc()).limit(500)
        ).all()
    )


@router.post("/projects/{project_id}/decisions", response_model=DecisionOut, status_code=status.HTTP_201_CREATED)
def create_decision(
    project_id: uuid.UUID,
    payload: DecisionIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Decision:
    load_project_for(db, user, project_id, ProjectRole.MEMBER)
    data = payload.model_dump()
    # Decisions always start as proposed. Nothing (including AI-generated text) is accepted automatically.
    decision = Decision(
        project_id=project_id,
        status=DecisionStatus.PROPOSED.value,
        proposer_user_id=user.id,
        owner_user_id=user.id,
        current_revision=1,
        **{k: v for k, v in data.items() if k != "status"},
    )
    db.add(decision)
    db.flush()
    db.add(
        DecisionRevision(
            decision_id=decision.id, revision_no=1, snapshot=_snapshot(decision), changed_by_user_id=user.id
        )
    )
    envelope = record_event(
        db,
        project_id=project_id,
        event_type="decision.updated",
        actor_user_id=user.id,
        payload={"decision_id": str(decision.id), "revision": 1, "status": decision.status},
    )
    record_audit(
        db,
        action="decision.created",
        actor_user_id=user.id,
        project_id=project_id,
        target_type="decision",
        target_id=str(decision.id),
        request_id=_req_id(request),
    )
    db.commit()
    db.refresh(decision)
    hub.publish(project_id, envelope)
    return decision


@router.get("/projects/{project_id}/decisions/{decision_id}", response_model=DecisionOut)
def get_decision(
    project_id: uuid.UUID, decision_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> Decision:
    load_project_for(db, user, project_id)
    return _decision_in_project(db, project_id, decision_id)


@router.get("/projects/{project_id}/decisions/{decision_id}/revisions", response_model=list[DecisionRevisionOut])
def list_decision_revisions(
    project_id: uuid.UUID, decision_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[DecisionRevision]:
    load_project_for(db, user, project_id)
    _decision_in_project(db, project_id, decision_id)
    return list(
        db.scalars(
            select(DecisionRevision)
            .where(DecisionRevision.decision_id == decision_id)
            .order_by(DecisionRevision.revision_no)
        ).all()
    )


@router.patch("/projects/{project_id}/decisions/{decision_id}", response_model=DecisionOut)
def update_decision(
    project_id: uuid.UUID,
    decision_id: uuid.UUID,
    payload: DecisionPatch,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Decision:
    _, membership = load_project_for(db, user, project_id, ProjectRole.MEMBER)
    decision = _decision_in_project(db, project_id, decision_id)
    changes = payload.model_dump(exclude_unset=True)
    is_moderator = ROLE_RANK[membership.role] >= ROLE_RANK[ProjectRole.ADMIN.value]
    is_decision_owner = decision.owner_user_id == user.id

    new_status = changes.get("status")
    if new_status is not None:
        if new_status not in VALID_DECISION_STATUS:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid status")
        # Only the decision owner or a project admin may move a decision to accepted/rejected/superseded.
        if new_status != DecisionStatus.PROPOSED.value and not (is_decision_owner or is_moderator):
            record_audit(
                db,
                action="decision.status_denied",
                actor_user_id=user.id,
                project_id=project_id,
                target_type="decision",
                target_id=str(decision.id),
                outcome=AuditOutcome.DENIED,
                request_id=_req_id(request),
            )
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Only the decision owner can change status"
            )

    for field, value in changes.items():
        if value is None and field in ("title", "statement", "status", "options"):
            continue
        setattr(decision, field, value)
    decision.current_revision += 1
    decision.updated_at = utcnow()
    db.add(
        DecisionRevision(
            decision_id=decision.id,
            revision_no=decision.current_revision,
            snapshot=_snapshot(decision),
            changed_by_user_id=user.id,
        )
    )
    envelope = record_event(
        db,
        project_id=project_id,
        event_type="decision.updated",
        actor_user_id=user.id,
        payload={"decision_id": str(decision.id), "revision": decision.current_revision, "status": decision.status},
    )
    record_audit(
        db,
        action="decision.updated",
        actor_user_id=user.id,
        project_id=project_id,
        target_type="decision",
        target_id=str(decision.id),
        request_id=_req_id(request),
    )
    db.commit()
    db.refresh(decision)
    hub.publish(project_id, envelope)
    return decision
