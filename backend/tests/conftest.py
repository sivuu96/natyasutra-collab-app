import os
from collections.abc import Callable, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.base import Base
from app.db.session import make_engine, make_session_factory
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    # Default: fast in-memory SQLite. Set TEST_DATABASE_URL to run the whole suite on PostgreSQL.
    database_url = os.environ.get("TEST_DATABASE_URL") or "sqlite:///:memory:"
    return Settings(environment="test", database_url=database_url, log_level="WARNING")


@pytest.fixture
def engine(settings: Settings) -> Iterator[Engine]:
    eng = make_engine(settings.database_url)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return make_session_factory(engine)


@pytest.fixture
def db(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def app(settings: Settings, engine: Engine) -> FastAPI:
    return create_app(settings=settings, engine=engine)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def client_factory(app: FastAPI) -> Callable[[], TestClient]:
    """Each call returns a client with its own cookie jar, i.e. a separate browser session."""
    clients: list[TestClient] = []

    def make() -> TestClient:
        test_client = TestClient(app)
        test_client.__enter__()
        clients.append(test_client)
        return test_client

    yield make
    for test_client in clients:
        test_client.__exit__(None, None, None)


@pytest.fixture
def pg_url() -> str:
    """Real PostgreSQL URL for integration tests. Skipped when TEST_DATABASE_URL is unset."""
    url = os.environ.get("TEST_DATABASE_URL", "")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set; PostgreSQL integration tests skipped")
    return url
