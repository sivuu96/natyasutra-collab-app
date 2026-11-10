"""Teams, projects and project membership."""

import re
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas import (
    MemberIn,
    MemberOut,
    MemberRoleIn,
    ProjectIn,
    ProjectOut,
    TeamIn,
    TeamOut,
)
from app.deps import get_current_user, get_db
from app.models import Channel, Project, ProjectMembership, Team, TeamMembership, User
from app.models.enums import ProjectRole, TeamRole
from app.services.audit import record_audit
from app.services.authz import ROLE_RANK, load_project_for

router = APIRouter(tags=["workspace"])

VALID_PROJECT_ROLES = {role.value for role in ProjectRole}


def _slugify(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "item"
    return f"{base}-{secrets.token_hex(3)}"


def _req_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


# ---- teams ------------------------------------------------------------------------------------


@router.post("/teams", response_model=TeamOut, status_code=status.HTTP_201_CREATED)
def create_team(
    payload: TeamIn, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> Team:
    team = Team(name=payload.name.strip(), slug=_slugify(payload.name))
    db.add(team)
    db.flush()
    db.add(TeamMembership(team_id=team.id, user_id=user.id, role=TeamRole.OWNER.value))
    record_audit(
        db,
        action="team.created",
        actor_user_id=user.id,
        target_type="team",
        target_id=str(team.id),
        request_id=_req_id(request),
    )
    db.commit()
    db.refresh(team)
    return team


@router.get("/teams", response_model=list[TeamOut])
def list_teams(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[Team]:
    return list(
        db.scalars(
            select(Team)
            .join(TeamMembership, TeamMembership.team_id == Team.id)
            .where(TeamMembership.user_id == user.id)
            .order_by(Team.name)
        ).all()
    )


@router.post("/teams/{team_id}/projects", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(
    team_id: uuid.UUID,
    payload: ProjectIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectOut:
    team_membership = db.scalar(
        select(TeamMembership).where(TeamMembership.team_id == team_id, TeamMembership.user_id == user.id)
    )
    if team_membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if team_membership.role not in (TeamRole.OWNER.value, TeamRole.ADMIN.value):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")

    project = Project(
        team_id=team_id, name=payload.name.strip(), slug=_slugify(payload.name), description=payload.description
    )
    db.add(project)
    db.flush()
    db.add(ProjectMembership(project_id=project.id, user_id=user.id, role=ProjectRole.OWNER.value))
    db.add(Channel(project_id=project.id, name="general", topic="Project-wide discussion"))
    record_audit(
        db,
        action="project.created",
        actor_user_id=user.id,
        project_id=project.id,
        target_type="project",
        target_id=str(project.id),
        request_id=_req_id(request),
    )
    db.commit()
    db.refresh(project)
    return ProjectOut(
        id=project.id,
        team_id=project.team_id,
        name=project.name,
        slug=project.slug,
        description=project.description,
        role=ProjectRole.OWNER.value,
    )


# ---- projects ---------------------------------------------------------------------------------


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[ProjectOut]:
    rows = db.execute(
        select(Project, ProjectMembership.role)
        .join(ProjectMembership, ProjectMembership.project_id == Project.id)
        .where(ProjectMembership.user_id == user.id)
        .order_by(Project.name)
    ).all()
    return [
        ProjectOut(id=p.id, team_id=p.team_id, name=p.name, slug=p.slug, description=p.description, role=role)
        for p, role in rows
    ]


@router.get("/projects/{project_id}", response_model=ProjectOut)
def get_project(
    project_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> ProjectOut:
    project, membership = load_project_for(db, user, project_id)
    return ProjectOut(
        id=project.id,
        team_id=project.team_id,
        name=project.name,
        slug=project.slug,
        latest_sequence=project.event_seq,
        description=project.description,
        role=membership.role,
    )


# ---- membership -------------------------------------------------------------------------------


def _members(db: Session, project_id: uuid.UUID) -> list[MemberOut]:
    rows = db.execute(
        select(ProjectMembership, User)
        .join(User, User.id == ProjectMembership.user_id)
        .where(ProjectMembership.project_id == project_id)
        .order_by(User.display_name)
    ).all()
    return [
        MemberOut(user_id=u.id, email=u.email, display_name=u.display_name, role=m.role, handle=u.handle)
        for m, u in rows
    ]


@router.get("/projects/{project_id}/members", response_model=list[MemberOut])
def list_members(
    project_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[MemberOut]:
    load_project_for(db, user, project_id)
    return _members(db, project_id)


def _check_can_assign(actor_role: str, target_role: str, current_target_role: str | None = None) -> None:
    """Only owners may grant, change or remove owner. Admins may manage the rest."""
    if target_role not in VALID_PROJECT_ROLES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid role")
    involves_owner = target_role == ProjectRole.OWNER.value or current_target_role == ProjectRole.OWNER.value
    if involves_owner and actor_role != ProjectRole.OWNER.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only an owner can change owners")
    if ROLE_RANK[target_role] > ROLE_RANK[actor_role]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot grant a higher role than your own")


def _owner_count(db: Session, project_id: uuid.UUID) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(ProjectMembership)
            .where(ProjectMembership.project_id == project_id, ProjectMembership.role == ProjectRole.OWNER.value)
        )
        or 0
    )


@router.post("/projects/{project_id}/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
def add_member(
    project_id: uuid.UUID,
    payload: MemberIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MemberOut:
    _, actor = load_project_for(db, user, project_id, ProjectRole.ADMIN)
    _check_can_assign(actor.role, payload.role)
    target = db.scalar(select(User).where(User.email == payload.email.lower()))
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No user with that email")
    if (
        db.scalar(
            select(ProjectMembership.id).where(
                ProjectMembership.project_id == project_id, ProjectMembership.user_id == target.id
            )
        )
        is not None
    ):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Already a member")
    db.add(ProjectMembership(project_id=project_id, user_id=target.id, role=payload.role))
    record_audit(
        db,
        action="project.member_added",
        actor_user_id=user.id,
        project_id=project_id,
        target_type="user",
        target_id=str(target.id),
        request_id=_req_id(request),
    )
    db.commit()
    return MemberOut(user_id=target.id, email=target.email, display_name=target.display_name, role=payload.role)


@router.patch("/projects/{project_id}/members/{member_user_id}", response_model=MemberOut)
def change_member_role(
    project_id: uuid.UUID,
    member_user_id: uuid.UUID,
    payload: MemberRoleIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MemberOut:
    _, actor = load_project_for(db, user, project_id, ProjectRole.ADMIN)
    membership = db.scalar(
        select(ProjectMembership).where(
            ProjectMembership.project_id == project_id, ProjectMembership.user_id == member_user_id
        )
    )
    if membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    _check_can_assign(actor.role, payload.role, membership.role)
    if (
        membership.role == ProjectRole.OWNER.value
        and payload.role != ProjectRole.OWNER.value
        and _owner_count(db, project_id) <= 1
    ):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A project must keep one owner")
    membership.role = payload.role
    record_audit(
        db,
        action="project.member_role_changed",
        actor_user_id=user.id,
        project_id=project_id,
        target_type="user",
        target_id=str(member_user_id),
        request_id=_req_id(request),
    )
    db.commit()
    target = db.get(User, member_user_id)
    assert target is not None
    return MemberOut(user_id=target.id, email=target.email, display_name=target.display_name, role=membership.role)


@router.delete("/projects/{project_id}/members/{member_user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(
    project_id: uuid.UUID,
    member_user_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    _, actor = load_project_for(db, user, project_id, ProjectRole.ADMIN)
    membership = db.scalar(
        select(ProjectMembership).where(
            ProjectMembership.project_id == project_id, ProjectMembership.user_id == member_user_id
        )
    )
    if membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    _check_can_assign(actor.role, membership.role, membership.role)
    if membership.role == ProjectRole.OWNER.value and _owner_count(db, project_id) <= 1:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A project must keep one owner")
    db.delete(membership)
    record_audit(
        db,
        action="project.member_removed",
        actor_user_id=user.id,
        project_id=project_id,
        target_type="user",
        target_id=str(member_user_id),
        request_id=_req_id(request),
    )
    db.commit()
