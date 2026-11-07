"""Seed local development data: two users, one team, one project, channels, a task and a decision.

Usage (from backend/):
    .venv\\Scripts\\python.exe -m scripts.seed

Idempotent: re-running does nothing if the seed team already exists. A random password is generated
for each run and printed ONCE; nothing sensitive is written to the repository.
"""

import secrets
import sys

from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.session import make_engine, make_session_factory
from app.models import Channel, Decision, DecisionRevision, Project, ProjectMembership, Task, Team, TeamMembership, User
from app.models.enums import DecisionStatus, ProjectRole, TaskPriority, TaskStatus, TeamRole
from app.services.events import record_event

SEED_TEAM_SLUG = "natyasutra-dev"
PEOPLE = [
    ("alice@example.com", "Alice Guru-Dev", ProjectRole.OWNER),
    ("bob@example.com", "Bob Frontend", ProjectRole.MEMBER),
]


def main() -> int:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    password = secrets.token_urlsafe(12)

    with session_factory() as db:
        if db.scalar(select(Team.id).where(Team.slug == SEED_TEAM_SLUG)) is not None:
            print("Seed data already present; nothing to do.")
            return 0

        users: dict[str, User] = {}
        for email, name, _ in PEOPLE:
            user = User(email=email, display_name=name, password_hash=hash_password(password))
            db.add(user)
            users[email] = user
        team = Team(name="NatyaSutra Development", slug=SEED_TEAM_SLUG)
        db.add(team)
        db.flush()
        for user in users.values():
            db.add(
                TeamMembership(
                    team_id=team.id,
                    user_id=user.id,
                    role=TeamRole.OWNER.value if user.email == PEOPLE[0][0] else TeamRole.MEMBER.value,
                )
            )

        project = Project(
            team_id=team.id,
            name="NatyaSutra Port",
            slug="natyasutra-port",
            description="Porting the NatyaSutra platform to the collaboration hub.",
        )
        db.add(project)
        db.flush()
        for email, _, role in PEOPLE:
            db.add(ProjectMembership(project_id=project.id, user_id=users[email].id, role=role.value))
        for name in ("general", "product", "architecture", "frontend", "backend"):
            db.add(Channel(project_id=project.id, name=name))
        db.flush()

        alice = users[PEOPLE[0][0]]
        bob = users[PEOPLE[1][0]]
        db.add(
            Task(
                project_id=project.id,
                title="Scaffold collaboration hub frontend",
                description="Vite + React + TypeScript shell with auth and workspace navigation.",
                acceptance_criteria="Login works; project list loads; channel view streams messages.",
                status=TaskStatus.IN_PROGRESS.value,
                priority=TaskPriority.HIGH.value,
                creator_user_id=alice.id,
                assignee_user_id=bob.id,
            )
        )
        decision = Decision(
            project_id=project.id,
            title="Use PostgreSQL full-text search first",
            statement="Search uses PostgreSQL full-text; no separate search engine until proven insufficient.",
            status=DecisionStatus.PROPOSED.value,
            proposer_user_id=alice.id,
            owner_user_id=alice.id,
            options=[{"name": "PostgreSQL FTS"}, {"name": "External search engine"}],
            current_revision=1,
        )
        db.add(decision)
        db.flush()
        db.add(
            DecisionRevision(
                decision_id=decision.id,
                revision_no=1,
                snapshot={"title": decision.title, "statement": decision.statement, "status": decision.status},
                changed_by_user_id=alice.id,
            )
        )
        record_event(
            db,
            project_id=project.id,
            event_type="project.seeded",
            actor_user_id=alice.id,
            payload={"note": "development seed data"},
        )
        db.commit()

    print("Seed complete.")
    print(f"  Users: {', '.join(email for email, _, _ in PEOPLE)}")
    print(f"  Shared password for this seed run (shown once): {password}")
    engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
