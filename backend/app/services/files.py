"""Files an agent returned in its reply, and who may read them.

A file belongs to one run. Whoever can see the run can read its files: project members for a channel run, and the
session's visibility rules for a session run. Files are plain text and are only ever sent as attachments.
"""

import uuid
from collections.abc import Iterable

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AgentInvocation, AgentSession, User
from app.models.agents import InvocationFile
from app.models.enums import SessionVisibility
from app.services.authz import load_project_for

MAX_FILES_PER_RUN = 5


def load_run_file(
    db: Session, user: User, project_id: uuid.UUID, invocation_id: uuid.UUID, file_id: uuid.UUID
) -> InvocationFile:
    """The file, if the user may see its run. Anything else is 404, so the run's existence is not revealed."""
    load_project_for(db, user, project_id)
    invocation = db.scalar(
        select(AgentInvocation).where(AgentInvocation.id == invocation_id, AgentInvocation.project_id == project_id)
    )
    if invocation is None:
        raise _not_found()
    if invocation.session_id is not None:
        session = db.get(AgentSession, invocation.session_id)
        visible = session is not None and session.deleted_at is None and (
            session.owner_user_id == user.id or session.visibility == SessionVisibility.PROJECT.value
        )
        if not visible:
            raise _not_found()
    stored = db.scalar(
        select(InvocationFile).where(InvocationFile.id == file_id, InvocationFile.invocation_id == invocation_id)
    )
    if stored is None:
        raise _not_found()
    return stored


def files_by_invocation(db: Session, invocation_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, list[InvocationFile]]:
    ids = list(invocation_ids)
    if not ids:
        return {}
    rows = db.scalars(
        select(InvocationFile).where(InvocationFile.invocation_id.in_(ids)).order_by(InvocationFile.name)
    ).all()
    grouped: dict[uuid.UUID, list[InvocationFile]] = {}
    for row in rows:
        grouped.setdefault(row.invocation_id, []).append(row)
    return grouped


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


__all__ = ["MAX_FILES_PER_RUN", "files_by_invocation", "load_run_file"]
