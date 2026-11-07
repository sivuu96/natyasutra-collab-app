"""Integration tests that run the migrations against a real PostgreSQL database.

Set TEST_DATABASE_URL=postgresql+psycopg://user:pass@host:5432/db (a throwaway database).
These are skipped when the variable is unset, so CI without PostgreSQL still passes.
"""

from sqlalchemy import create_engine, inspect, text

from app.db.base import Base
from app.models import Project


def test_metadata_creates_on_postgres(pg_url: str) -> None:
    engine = create_engine(pg_url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    try:
        names = set(inspect(engine).get_table_names())
        assert {
            "users",
            "teams",
            "projects",
            "project_memberships",
            "messages",
            "tasks",
            "decisions",
            "audit_events",
        } <= names
        with engine.connect() as conn:
            assert conn.execute(text("SELECT 1")).scalar() == 1
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_project_fk_enforced_on_postgres(pg_url: str) -> None:
    import uuid

    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(pg_url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    try:
        session = sessionmaker(bind=engine)()
        try:
            session.add(Project(team_id=uuid.uuid4(), name="Orphan", slug="orphan"))
            try:
                session.commit()
                raised = False
            except IntegrityError:
                raised = True
            assert raised, "orphan project should violate team FK on PostgreSQL"
        finally:
            session.rollback()
            session.close()
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
