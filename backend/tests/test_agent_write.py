"""Agents that may commit and push: the flag, the protected branches, and what the exchange tells the runtime."""

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import update
from test_run_lease import _exchange, _start_run

from app.models.agents import Agent
from app.services.leases import write_allowed


@pytest.mark.parametrize(("ref", "allowed"), [("main", False), ("master", False), ("feature-x", True), ("v2", True)])
def test_protected_branches_are_never_writable(ref: str, allowed: bool) -> None:
    assert write_allowed(ref) is allowed


def _set_agent(app: FastAPI, invocation_id: uuid.UUID, *, can_write: bool, ref: str | None) -> None:
    from app.models.agents import AgentInvocation

    with app.state.session_factory() as db:
        agent_id = db.get(AgentInvocation, invocation_id).agent_id  # type: ignore[union-attr]
        db.execute(update(Agent).where(Agent.id == agent_id).values(can_write=can_write, default_ref=ref))
        db.commit()


def test_a_flagged_agent_on_a_branch_is_told_it_may_write(fake_app: FastAPI, fake_client: TestClient) -> None:
    run = _start_run(fake_app, fake_client, handle=f"writer{uuid.uuid4().hex[:6]}")
    _set_agent(fake_app, run["invocation_id"], can_write=True, ref="feature-x")

    body = _exchange(fake_client, run["token"], run["execution_name"]).json()

    assert body["can_write"] is True
    assert body["ref"] == "feature-x"


def test_an_agent_without_the_flag_is_read_only(fake_app: FastAPI, fake_client: TestClient) -> None:
    run = _start_run(fake_app, fake_client, handle=f"reader{uuid.uuid4().hex[:6]}")
    _set_agent(fake_app, run["invocation_id"], can_write=False, ref="feature-x")

    body = _exchange(fake_client, run["token"], run["execution_name"]).json()

    assert body["can_write"] is False


def test_a_flagged_agent_on_main_is_still_read_only(fake_app: FastAPI, fake_client: TestClient) -> None:
    run = _start_run(fake_app, fake_client, handle=f"mainer{uuid.uuid4().hex[:6]}")
    _set_agent(fake_app, run["invocation_id"], can_write=True, ref="main")

    body = _exchange(fake_client, run["token"], run["execution_name"]).json()

    assert body["can_write"] is False
