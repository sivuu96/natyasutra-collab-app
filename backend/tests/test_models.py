import datetime as dt
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Channel,
    Decision,
    Message,
    Project,
    ProjectMembership,
    Task,
    Team,
    TeamMembership,
    User,
)


def _seed(db: Session) -> dict[str, uuid.UUID]:
    owner = User(email="owner@example.com", display_name="Owner")
    other = User(email="other@example.com", display_name="Other")
    team = Team(name="NatyaSutra", slug="natyasutra")
    db.add_all([owner, other, team])
    db.flush()
    project = Project(team_id=team.id, name="Port", slug="port")
    db.add(project)
    db.flush()
    channel = Channel(project_id=project.id, name="general")
    db.add(channel)
    db.flush()
    db.add(TeamMembership(team_id=team.id, user_id=owner.id, role="owner"))
    db.add(ProjectMembership(project_id=project.id, user_id=owner.id, role="owner"))
    db.commit()
    return {
        "owner": owner.id,
        "other": other.id,
        "project": project.id,
        "channel": channel.id,
    }


def test_seed_and_query_project_scope(db: Session) -> None:
    ids = _seed(db)
    members = db.scalars(select(ProjectMembership).where(ProjectMembership.project_id == ids["project"])).all()
    assert [m.user_id for m in members] == [ids["owner"]]


def test_duplicate_project_membership_rejected(db: Session) -> None:
    ids = _seed(db)
    db.add(ProjectMembership(project_id=ids["project"], user_id=ids["owner"], role="member"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_invalid_project_role_rejected_by_check_constraint(db: Session) -> None:
    ids = _seed(db)
    db.add(ProjectMembership(project_id=ids["project"], user_id=ids["other"], role="superuser"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_invalid_task_status_rejected(db: Session) -> None:
    ids = _seed(db)
    db.add(Task(project_id=ids["project"], title="Bad", status="started"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_task_defaults_and_fields(db: Session) -> None:
    ids = _seed(db)
    task = Task(
        project_id=ids["project"],
        title="Port Mohiniyattam notes",
        due_date=dt.date(2026, 12, 1),
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    assert task.status == "backlog"
    assert task.priority == "medium"
    assert task.created_at is not None


def test_decision_defaults_proposed_and_revision_starts_at_one(db: Session) -> None:
    ids = _seed(db)
    decision = Decision(project_id=ids["project"], title="Use PostgreSQL", statement="Adopt PostgreSQL")
    db.add(decision)
    db.commit()
    db.refresh(decision)
    assert decision.status == "proposed"
    assert decision.current_revision == 1
    assert decision.options == []


def test_message_threading_and_soft_delete(db: Session) -> None:
    ids = _seed(db)
    root = Message(
        project_id=ids["project"],
        channel_id=ids["channel"],
        author_user_id=ids["owner"],
        body="Hi",
    )
    db.add(root)
    db.flush()
    reply = Message(
        project_id=ids["project"],
        channel_id=ids["channel"],
        parent_message_id=root.id,
        author_user_id=ids["other"],
        body="Hello",
    )
    db.add(reply)
    db.commit()
    db.refresh(reply)
    assert reply.parent_message_id == root.id
    assert reply.deleted_at is None


def test_channel_name_unique_per_project(db: Session) -> None:
    ids = _seed(db)
    db.add(Channel(project_id=ids["project"], name="general"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
