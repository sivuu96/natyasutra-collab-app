"""Engine and session factory. Built from settings; no global engine at import time."""

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def make_engine(database_url: str) -> Engine:
    if database_url.startswith("sqlite"):
        # In-memory SQLite must share one connection or each connection sees an empty database.
        is_memory = database_url in ("sqlite://", "sqlite:///:memory:")
        kwargs: dict[str, object] = {"connect_args": {"check_same_thread": False}}
        if is_memory:
            kwargs["poolclass"] = StaticPool
        engine = create_engine(database_url, **kwargs)  # type: ignore[arg-type]
    else:
        engine = create_engine(database_url, pool_pre_ping=True)
    if database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _enable_fk(dbapi_connection, _record):  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
