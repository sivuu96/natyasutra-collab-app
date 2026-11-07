"""Single authorisation point for project-scoped data.

Every project route calls `load_project_for`. Non-members receive 404 (never 403) so the
existence of a project is not revealed. Members with an insufficient role receive 403.
"""

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Project, ProjectMembership, User
from app.models.enums import ProjectRole

ROLE_RANK: dict[str, int] = {
    ProjectRole.VIEWER.value: 0,
    ProjectRole.MEMBER.value: 1,
    ProjectRole.ADMIN.value: 2,
    ProjectRole.OWNER.value: 3,
}


def get_membership(db: Session, user: User, project_id: uuid.UUID) -> ProjectMembership | None:
    return db.scalar(
        select(ProjectMembership).where(
            ProjectMembership.project_id == project_id,
            ProjectMembership.user_id == user.id,
        )
    )


def load_project_for(
    db: Session,
    user: User,
    project_id: uuid.UUID,
    min_role: ProjectRole = ProjectRole.VIEWER,
) -> tuple[Project, ProjectMembership]:
    membership = get_membership(db, user, project_id)
    if membership is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if ROLE_RANK[membership.role] < ROLE_RANK[min_role.value]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
    project = db.get(Project, project_id)
    if project is None:  # membership without project cannot normally exist; treat as absent
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return project, membership


def can_read(role: str) -> bool:
    return ROLE_RANK.get(role, -1) >= ROLE_RANK[ProjectRole.VIEWER.value]


def can_write(role: str) -> bool:
    return ROLE_RANK.get(role, -1) >= ROLE_RANK[ProjectRole.MEMBER.value]
