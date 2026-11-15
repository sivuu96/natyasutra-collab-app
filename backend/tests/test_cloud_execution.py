"""Cloud execution, end to end through the hub: a mention is queued and the worker dispatches it.

No test contacts Google Cloud. Dispatch to the service is tested in test_service_execution.py.
"""

import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from helpers import API, create_project, csrf_headers, general_channel, register
from pydantic import SecretStr
from sqlalchemy import select

from app.core.config import Settings
from app.models.agents import Agent, AgentInvocation, ProjectAgent
from app.models.enums import InvocationStatus
from app.models.execution import ExecutionOutbox
from app.services.execution import process_once


def test_dispatcher_key_is_not_shown_in_settings_repr() -> None:
    settings = Settings(environment="test", gcp_dispatcher_key=SecretStr("super-secret-value"))
    assert "super-secret-value" not in repr(settings)


def test_fake_is_the_default_mode() -> None:
    assert Settings(environment="test").execution_provider == "fake"


def test_mention_is_queued_and_the_worker_submits_it(fake_app: FastAPI, fake_client: TestClient) -> None:
    owner = register(fake_client, "cloud-owner@example.com", "Owner")
    project = create_project(fake_client, "Cloud project")
    project_id = project["id"]
    channel_id = general_channel(fake_client, project_id)
    owner_id = uuid.UUID(owner["id"])

    with fake_app.state.session_factory() as db:
        agent = Agent(
            owner_user_id=owner_id,
            handle="cloudclaude",
            display_name="Cloud Claude",
            harness="claude_code",
        )
        db.add(agent)
        db.flush()
        db.add(ProjectAgent(project_id=uuid.UUID(project_id), agent_id=agent.id, added_by_user_id=owner_id))
        db.commit()

    response = fake_client.post(
        f"{API}/projects/{project_id}/channels/{channel_id}/messages",
        json={"body": "@cloudclaude please review the port plan"},
        headers=csrf_headers(fake_client),
    )
    assert response.status_code == 201, response.text

    with fake_app.state.session_factory() as db:
        invocation = db.scalar(select(AgentInvocation))
        assert invocation is not None
        assert invocation.status == InvocationStatus.QUEUED.value
        outbox = db.scalar(select(ExecutionOutbox).where(ExecutionOutbox.invocation_id == invocation.id))
        assert outbox is not None and outbox.status == "pending"
        invocation_id = invocation.id

    provider = fake_app.state.execution_provider
    assert provider is not None
    processed = process_once(fake_app.state.session_factory, provider, owner="test-worker")

    assert processed == 1
    with fake_app.state.session_factory() as db:
        invocation = db.get(AgentInvocation, invocation_id)
        assert invocation is not None
        assert invocation.status == InvocationStatus.QUEUED.value  # the runtime's lease exchange starts the run
        outbox = db.scalar(select(ExecutionOutbox).where(ExecutionOutbox.invocation_id == invocation_id))
        assert outbox is not None and outbox.status == "done"
    assert [s[0] for s in provider.submitted] == [invocation_id]  # type: ignore[attr-defined]
