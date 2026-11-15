"""The exchange tells the runtime which turn it is in a session: the new message alone, and the turn before it.

The runtime uses these to resume its own copy of the conversation only when it holds that previous turn.
"""

import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from helpers import API, create_project, csrf_headers, general_channel, register
from test_run_lease import _exchange
from test_session_transcript import _finish_turn, _session_with_agent, _turn

from app.models.agents import Agent, ProjectAgent
from app.services.execution import FakeExecutionProvider, process_once


def _exchange_latest(app: FastAPI, client: TestClient) -> dict:
    provider = app.state.execution_provider
    assert isinstance(provider, FakeExecutionProvider)
    process_once(app.state.session_factory, provider, owner="test-worker")
    invocation_id, _, execution_name = provider.submitted[-1]
    response = _exchange(client, provider.lease_tokens[execution_name], execution_name)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["invocation_id"] == str(invocation_id)
    return body


def test_a_first_session_turn_has_no_previous_turn(fake_app: FastAPI, fake_client: TestClient) -> None:
    project_id, session_id = _session_with_agent(fake_app, fake_client, "ctx-first")
    _turn(fake_client, project_id, session_id, "first message")
    body = _exchange_latest(fake_app, fake_client)
    assert body["session_id"] == session_id
    assert body["message"] == "first message"
    assert body["previous_invocation_id"] is None


def test_a_follow_up_names_the_turn_before_it_and_carries_only_its_own_message(
    fake_app: FastAPI, fake_client: TestClient
) -> None:
    project_id, session_id = _session_with_agent(fake_app, fake_client, "ctx-follow")
    _turn(fake_client, project_id, session_id, "first message")
    first = _exchange_latest(fake_app, fake_client)
    _finish_turn(fake_app, session_id, "first reply")
    _turn(fake_client, project_id, session_id, "second message")
    second = _exchange_latest(fake_app, fake_client)
    assert second["previous_invocation_id"] == first["invocation_id"]
    assert second["message"] == "second message"
    # The transcript is still sent whole, for a runtime that has to start again.
    assert "first message" in second["prompt"] and "second message" in second["prompt"]


def test_a_mention_has_no_session_fields(fake_app: FastAPI, fake_client: TestClient) -> None:
    owner = register(fake_client, "ctx-mention-owner@example.com", "Owner")
    project_id = create_project(fake_client, "ctx mention project")["id"]
    owner_id = uuid.UUID(owner["id"])
    with fake_app.state.session_factory() as db:
        agent = Agent(owner_user_id=owner_id, handle="ctx-mention", display_name="Mention agent", harness="claude_code")
        db.add(agent)
        db.flush()
        db.add(ProjectAgent(project_id=uuid.UUID(project_id), agent_id=agent.id, added_by_user_id=owner_id))
        db.commit()
    channel = general_channel(fake_client, project_id)
    posted = fake_client.post(
        f"{API}/projects/{project_id}/channels/{channel}/messages",
        json={"body": "@ctx-mention please check"},
        headers=csrf_headers(fake_client),
    )
    assert posted.status_code == 201, posted.text
    body = _exchange_latest(fake_app, fake_client)
    assert body["session_id"] is None
    assert body["message"] is None
    assert body["previous_invocation_id"] is None


def test_a_failed_turn_is_still_the_previous_turn_the_hub_names(fake_app: FastAPI, fake_client: TestClient) -> None:
    project_id, session_id = _session_with_agent(fake_app, fake_client, "ctx-failed")
    _turn(fake_client, project_id, session_id, "first message")
    first = _exchange_latest(fake_app, fake_client)
    with fake_app.state.session_factory() as db:
        from app.models.agents import AgentInvocation
        from app.models.enums import InvocationStatus

        invocation = db.get(AgentInvocation, uuid.UUID(first["invocation_id"]))
        assert invocation is not None
        invocation.status = InvocationStatus.FAILED.value
        db.commit()
    _turn(fake_client, project_id, session_id, "second message")
    second = _exchange_latest(fake_app, fake_client)
    # The runtime discards its copy after a failed turn, so it will start again from the transcript.
    assert second["previous_invocation_id"] == first["invocation_id"]
