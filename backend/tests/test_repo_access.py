"""Repository access: a run only gets a repository on the hub's allow-list, and a refusal does not use the lease."""

import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from helpers import API
from sqlalchemy import Engine, select, update
from test_run_lease import _exchange, _start_run

from app.core.config import Settings
from app.main import create_app
from app.models.agents import Agent
from app.models.execution import InvocationLease


def _app_with_allowed(engine: Engine, allowed: list[str]) -> FastAPI:
    settings = Settings(
        environment="test",
        log_level="WARNING",
        execution_provider="fake",
        execution_worker_enabled=False,
        github_allowed_repos=allowed,
    )
    return create_app(settings=settings, engine=engine)


def _set_repo(app: FastAPI, invocation_id: uuid.UUID, repo: str | None) -> None:
    with app.state.session_factory() as db:
        from app.models.agents import AgentInvocation

        invocation = db.get(AgentInvocation, invocation_id)
        assert invocation is not None
        db.execute(
            update(Agent)
            .where(Agent.id == invocation.agent_id)
            .values(repository_full_name=repo, default_ref="main" if repo else None)
        )
        db.commit()


def test_an_allowed_repository_is_returned_with_its_branch(engine: Engine) -> None:
    app = _app_with_allowed(engine, ["sivuu96/Natyasutra"])
    with TestClient(app) as client:
        run = _start_run(app, client, handle=f"repo{uuid.uuid4().hex[:6]}")
        _set_repo(app, run["invocation_id"], "sivuu96/Natyasutra")

        body = _exchange(client, run["token"], run["execution_name"]).json()

    assert body["repository"] == "sivuu96/Natyasutra"
    assert body["ref"] == "main"


def test_a_repository_off_the_allow_list_is_refused_and_the_lease_is_kept(engine: Engine) -> None:
    app = _app_with_allowed(engine, ["sivuu96/Natyasutra"])
    with TestClient(app) as client:
        run = _start_run(app, client, handle=f"repo{uuid.uuid4().hex[:6]}")
        _set_repo(app, run["invocation_id"], "someone-else/secret-repo")

        response = _exchange(client, run["token"], run["execution_name"])

        assert response.status_code == 409
        assert response.json()["detail"] == "repository_not_allowed"
        with app.state.session_factory() as db:
            from app.services.execution import hash_token

            lease = db.scalar(select(InvocationLease).where(InvocationLease.token_hash == hash_token(run["token"])))
            assert lease is not None and lease.used_at is None  # refused before the lease was burned

        # Once the agent is fixed, the same lease still works.
        _set_repo(app, run["invocation_id"], "sivuu96/Natyasutra")
        assert _exchange(client, run["token"], run["execution_name"]).status_code == 200


def test_a_run_without_a_repository_returns_none(engine: Engine) -> None:
    app = _app_with_allowed(engine, [])
    with TestClient(app) as client:
        run = _start_run(app, client, handle=f"repo{uuid.uuid4().hex[:6]}")
        body = _exchange(client, run["token"], run["execution_name"]).json()
    assert body["repository"] is None
    assert body["ref"] is None
    assert API  # the API prefix helper stays imported for the internal path checks
