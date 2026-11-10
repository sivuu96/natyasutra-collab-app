"""A cloud session turn carries the session's earlier turns, so a second message continues the same conversation."""

import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from helpers import API, create_project, csrf_headers, register
from test_run_lease import _exchange

from app.models.agents import Agent, AgentInvocation, ProjectAgent
from app.models.enums import InvocationStatus
from app.models.sessions import AgentSessionMessage
from app.services.execution import FakeExecutionProvider, process_once


def _session_with_agent(app: FastAPI, client: TestClient, handle: str) -> tuple[str, str]:
    owner = register(client, f"{handle}-owner@example.com", "Owner")
    project_id = create_project(client, f"{handle} project")["id"]
    owner_id = uuid.UUID(owner["id"])
    with app.state.session_factory() as db:
        agent = Agent(
            owner_user_id=owner_id,
            handle=handle,
            display_name="Session test agent",
            harness="claude_code",
        )
        db.add(agent)
        db.flush()
        db.add(ProjectAgent(project_id=uuid.UUID(project_id), agent_id=agent.id, added_by_user_id=owner_id))
        db.commit()
        agent_id = str(agent.id)
    created = client.post(
        f"{API}/projects/{project_id}/sessions",
        json={"agent_id": agent_id, "title": "Chat", "visibility": "private"},
        headers=csrf_headers(client),
    )
    assert created.status_code == 201, created.text
    return project_id, created.json()["id"]


def _turn(client: TestClient, project_id: str, session_id: str, body: str) -> None:
    response = client.post(
        f"{API}/projects/{project_id}/sessions/{session_id}/turns",
        json={"body": body},
        headers=csrf_headers(client),
    )
    assert response.status_code == 201, response.text


def _run_and_exchange(app: FastAPI, client: TestClient) -> str:
    provider = app.state.execution_provider
    assert isinstance(provider, FakeExecutionProvider)
    process_once(app.state.session_factory, provider, owner="test-worker")
    invocation_id, _, execution_name = provider.submitted[-1]
    response = _exchange(client, provider.lease_tokens[execution_name], execution_name)
    assert response.status_code == 200, response.text
    return response.json()["prompt"]


def _finish_turn(app: FastAPI, session_id: str, reply: str) -> None:
    """Stand in for the runtime: complete the latest turn and record the agent's reply."""
    with app.state.session_factory() as db:
        invocation = (
            db.query(AgentInvocation)
            .filter(AgentInvocation.session_id == uuid.UUID(session_id))
            .order_by(AgentInvocation.created_at.desc())
            .first()
        )
        assert invocation is not None
        invocation.status = InvocationStatus.COMPLETED.value
        db.add(
            AgentSessionMessage(
                session_id=invocation.session_id,
                project_id=invocation.project_id,
                role="agent",
                agent_id=invocation.agent_id,
                invocation_id=invocation.id,
                body=reply,
            )
        )
        db.commit()


def test_the_first_turn_has_no_history(fake_app: FastAPI, fake_client: TestClient) -> None:
    project_id, session_id = _session_with_agent(fake_app, fake_client, "firstturn")
    _turn(fake_client, project_id, session_id, "What is the schema for tasks?")

    prompt = _run_and_exchange(fake_app, fake_client)

    assert prompt == "What is the schema for tasks?"


def test_the_second_turn_carries_the_first_turn_and_its_reply(fake_app: FastAPI, fake_client: TestClient) -> None:
    project_id, session_id = _session_with_agent(fake_app, fake_client, "secondturn")
    _turn(fake_client, project_id, session_id, "Remember the word PINEAPPLE.")
    _run_and_exchange(fake_app, fake_client)
    _finish_turn(fake_app, session_id, "Noted: PINEAPPLE.")

    _turn(fake_client, project_id, session_id, "Which word did I ask you to remember?")
    prompt = _run_and_exchange(fake_app, fake_client)

    assert "Owner: Remember the word PINEAPPLE." in prompt
    assert "@secondturn: Noted: PINEAPPLE." in prompt
    assert prompt.endswith("New message:\nWhich word did I ask you to remember?")
    assert prompt.count("Which word did I ask you to remember?") == 1


def test_the_stored_invocation_prompt_is_not_changed(fake_app: FastAPI, fake_client: TestClient) -> None:
    # The transcript goes only to the run. The invocation keeps the bare message, which realtime events carry.
    project_id, session_id = _session_with_agent(fake_app, fake_client, "storedprompt")
    _turn(fake_client, project_id, session_id, "first")
    _run_and_exchange(fake_app, fake_client)
    _finish_turn(fake_app, session_id, "reply")
    _turn(fake_client, project_id, session_id, "second")
    _run_and_exchange(fake_app, fake_client)

    with fake_app.state.session_factory() as db:
        latest = (
            db.query(AgentInvocation)
            .filter(AgentInvocation.session_id == uuid.UUID(session_id))
            .order_by(AgentInvocation.created_at.desc())
            .first()
        )
        assert latest is not None
        assert latest.prompt == "second"
