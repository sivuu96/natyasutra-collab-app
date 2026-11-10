"""Cloud execution: provider HTTP contract (mocked), settings validation, and the mention-to-delivery path.

No test contacts Google Cloud. The provider is exercised with httpx.MockTransport and fake credentials.
"""

import base64
import json
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from helpers import API, create_project, csrf_headers, general_channel, register
from pydantic import SecretStr, ValidationError
from sqlalchemy import Engine, select

from app.core.config import Settings
from app.main import create_app
from app.models.bridge import Agent, AgentInvocation, ProjectAgent
from app.models.enums import InvocationStatus
from app.models.execution import ExecutionOutbox
from app.services.execution import ProviderError, process_once
from app.services.providers.cloud_run import CloudRunJobsProvider, load_dispatcher_credentials

EXECUTION_NAME = "projects/natyasutra-agents/locations/us-west1/jobs/collab-agent-runtime/executions/e1"


class FakeCredentials:
    def __init__(self, valid: bool = True) -> None:
        self.valid = valid
        self.token = "fake-token"
        self.refresh_calls = 0

    def refresh(self, _request: object) -> None:
        self.refresh_calls += 1
        self.valid = True


def _provider(handler: Any, credentials: FakeCredentials | None = None) -> CloudRunJobsProvider:
    return CloudRunJobsProvider(
        project_id="natyasutra-agents",
        region="us-west1",
        job_name="collab-agent-runtime",
        hub_base_url="https://hub.example",
        credentials=credentials or FakeCredentials(),
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )


# ---- provider HTTP contract -------------------------------------------------------------------------


def test_submit_starts_the_job_with_identifiers_only() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, json={"metadata": {"name": EXECUTION_NAME}})

    invocation = uuid.uuid4()
    name = _provider(handler).submit(invocation_id=invocation, dedupe_key="dispatch:abc")

    assert name == EXECUTION_NAME
    assert seen["url"] == (
        "https://run.googleapis.com/v2/projects/natyasutra-agents/locations/us-west1/jobs/collab-agent-runtime:run"
    )
    assert seen["auth"] == "Bearer fake-token"
    env = {item["name"]: item["value"] for item in seen["body"]["overrides"]["containerOverrides"][0]["env"]}
    assert env == {
        "INVOCATION_ID": str(invocation),
        "DEDUPE_KEY": "dispatch:abc",
        "HUB_BASE_URL": "https://hub.example",
    }


def test_submit_refreshes_expired_credentials_before_calling() -> None:
    credentials = FakeCredentials(valid=False)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"metadata": {"name": EXECUTION_NAME}})

    _provider(handler, credentials).submit(invocation_id=uuid.uuid4(), dedupe_key="k")
    assert credentials.refresh_calls == 1


def test_http_error_becomes_a_provider_error() -> None:
    provider = _provider(lambda _r: httpx.Response(500, text="boom"))
    with pytest.raises(ProviderError, match="HTTP 500"):
        provider.submit(invocation_id=uuid.uuid4(), dedupe_key="k")


def test_transport_error_becomes_a_provider_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    with pytest.raises(ProviderError, match="transport error"):
        _provider(handler).submit(invocation_id=uuid.uuid4(), dedupe_key="k")


def test_missing_execution_name_is_an_error() -> None:
    provider = _provider(lambda _r: httpx.Response(200, json={"name": "operation"}))
    with pytest.raises(ProviderError, match="execution name"):
        provider.submit(invocation_id=uuid.uuid4(), dedupe_key="k")


def test_cancel_posts_to_the_execution_and_treats_404_as_done() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(404 if len(calls) == 2 else 200, json={})

    provider = _provider(handler)
    provider.cancel(EXECUTION_NAME)
    provider.cancel(EXECUTION_NAME)  # already gone: no error
    assert calls[0] == f"https://run.googleapis.com/v2/{EXECUTION_NAME}:cancel"


def test_cancel_failure_raises() -> None:
    provider = _provider(lambda _r: httpx.Response(503))
    with pytest.raises(ProviderError):
        provider.cancel(EXECUTION_NAME)


def test_malformed_dispatcher_key_is_rejected() -> None:
    with pytest.raises(ValueError, match="GCP_DISPATCHER_KEY"):
        load_dispatcher_credentials("not base64 !!")
    with pytest.raises(ValueError, match="GCP_DISPATCHER_KEY"):
        load_dispatcher_credentials(base64.b64encode(b"{not json").decode())


# ---- settings validation ----------------------------------------------------------------------------


def test_cloud_run_mode_requires_project_key_and_hub_url() -> None:
    with pytest.raises(ValidationError, match="GCP_PROJECT_ID"):
        Settings(environment="test", execution_provider="cloud_run", hub_base_url="https://hub.example")
    with pytest.raises(ValidationError, match="GCP_DISPATCHER_KEY"):
        Settings(
            environment="test",
            execution_provider="cloud_run",
            gcp_project_id="natyasutra-agents",
            hub_base_url="https://hub.example",
        )


def test_dispatcher_key_is_not_shown_in_settings_repr() -> None:
    settings = Settings(environment="test", gcp_dispatcher_key=SecretStr("super-secret-value"))
    assert "super-secret-value" not in repr(settings)


def test_bridge_is_the_default_mode() -> None:
    assert Settings(environment="test").execution_provider == "bridge"


# ---- mention to delivery, through the API ----------------------------------------------------------


@pytest.fixture
def fake_app(engine: Engine) -> FastAPI:
    settings = Settings(
        environment="test",
        log_level="WARNING",
        execution_provider="fake",
        execution_worker_enabled=False,  # the test drives the worker explicitly
    )
    return create_app(settings=settings, engine=engine)


@pytest.fixture
def fake_client(fake_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(fake_app) as client:
        yield client


def test_mention_is_queued_and_the_worker_delivers_it(fake_app: FastAPI, fake_client: TestClient) -> None:
    owner = register(fake_client, "cloud-owner@example.com", "Owner")
    project = create_project(fake_client, "Cloud project")
    project_id = project["id"]
    channel_id = general_channel(fake_client, project_id)
    owner_id = uuid.UUID(owner["id"])

    with fake_app.state.session_factory() as db:
        agent = Agent(
            owner_user_id=owner_id,
            device_id=None,  # cloud agent: no developer machine
            handle="cloudclaude",
            display_name="Cloud Claude",
            harness="claude_code",
            capabilities=[],
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
        assert invocation.status == InvocationStatus.DELIVERED.value
        outbox = db.scalar(select(ExecutionOutbox).where(ExecutionOutbox.invocation_id == invocation_id))
        assert outbox is not None and outbox.status == "done"
    assert [s[0] for s in provider.submitted] == [invocation_id]  # type: ignore[attr-defined]
