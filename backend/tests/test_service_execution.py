"""Warm-instance execution: the dispatch contract to the Cloud Run service (mocked, no Google calls)."""

import base64
import json
import uuid
from typing import Any

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from app.services.execution import ProviderError
from app.services.providers import build_execution_provider
from app.services.providers import cloud_run_service as service_module
from app.services.providers.cloud_run_service import CloudRunServiceProvider, load_dispatcher_info

SERVICE_URL = "https://collab-agent-runtime-service-abc.a.run.app"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service_module.time, "sleep", lambda _seconds: None)


def _provider(handler: Any, token: Any = lambda: "id-token") -> CloudRunServiceProvider:
    return CloudRunServiceProvider(
        service_url=SERVICE_URL,
        token_source=token,
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        background=False,
    )


def test_submit_posts_the_run_with_an_identity_token_and_returns_the_execution_name() -> None:
    seen: dict[str, Any] = {}
    invocation_id = uuid.uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"exit_code": 0})

    name = _provider(handler).submit(invocation_id=invocation_id, dedupe_key="k", lease_token="lease-1")

    assert seen["url"] == f"{SERVICE_URL}/runs"
    assert seen["auth"] == "Bearer id-token"
    assert seen["body"] == {"execution_name": name, "lease": "lease-1"}
    assert name.startswith(f"service/{invocation_id}/")


def test_each_submit_gets_its_own_execution_name() -> None:
    provider = _provider(lambda request: httpx.Response(200, json={"exit_code": 0}))
    invocation_id = uuid.uuid4()
    first = provider.submit(invocation_id=invocation_id, dedupe_key="k", lease_token="a")
    second = provider.submit(invocation_id=invocation_id, dedupe_key="k", lease_token="b")
    assert first != second


def test_a_conflict_means_the_run_is_already_going() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(409, json={"detail": "already_running"})

    assert _provider(handler).submit(invocation_id=uuid.uuid4(), dedupe_key="k", lease_token="l")
    assert len(calls) == 1


def test_a_transient_failure_is_retried_and_then_succeeds() -> None:
    responses = iter([httpx.ConnectError("reset"), httpx.Response(503), httpx.Response(200, json={})])
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        outcome = next(responses)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    _provider(handler).submit(invocation_id=uuid.uuid4(), dedupe_key="k", lease_token="l")
    assert len(calls) == 3


def test_a_refusal_is_not_retried() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(403, json={})

    _provider(handler).submit(invocation_id=uuid.uuid4(), dedupe_key="k", lease_token="l")
    assert len(calls) == 1


def test_a_credential_failure_is_raised_so_the_outbox_retries_it() -> None:
    def broken_token() -> str:
        raise RuntimeError("no key")

    with pytest.raises(ProviderError):
        _provider(lambda request: httpx.Response(200), token=broken_token).submit(
            invocation_id=uuid.uuid4(), dedupe_key="k", lease_token="l"
        )


def test_the_lease_is_not_logged_when_dispatch_fails(caplog: pytest.LogCaptureFixture) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={})

    _provider(handler).submit(invocation_id=uuid.uuid4(), dedupe_key="k", lease_token="secret-lease-value")
    assert "secret-lease-value" not in caplog.text


def test_service_mode_requires_the_key_and_the_service_url() -> None:
    with pytest.raises(ValidationError, match="GCP_DISPATCHER_KEY"):
        Settings(environment="test", execution_provider="cloud_run_service", runtime_service_url=SERVICE_URL)
    with pytest.raises(ValidationError, match="RUNTIME_SERVICE_URL"):
        Settings(
            environment="test",
            execution_provider="cloud_run_service",
            gcp_dispatcher_key=SecretStr("e30="),
        )


def test_malformed_dispatcher_key_is_rejected() -> None:
    with pytest.raises(ValueError, match="GCP_DISPATCHER_KEY"):
        load_dispatcher_info("not base64 !!")
    with pytest.raises(ValueError, match="GCP_DISPATCHER_KEY"):
        load_dispatcher_info(base64.b64encode(b"{not json").decode())
    with pytest.raises(ValueError, match="GCP_DISPATCHER_KEY"):
        load_dispatcher_info(base64.b64encode(b"[1, 2]").decode())


def test_service_mode_builds_the_service_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    key = base64.b64encode(json.dumps({"type": "service_account"}).encode()).decode()
    seen: dict[str, Any] = {}

    def fake_source(info: dict[str, Any], audience: str) -> Any:
        seen["info"] = info
        seen["audience"] = audience
        return lambda: "t"

    monkeypatch.setattr("app.services.providers.id_token_source", fake_source)
    settings = Settings(
        environment="test",
        execution_provider="cloud_run_service",
        runtime_service_url=SERVICE_URL,
        gcp_dispatcher_key=SecretStr(key),
    )
    provider = build_execution_provider(settings)
    assert isinstance(provider, CloudRunServiceProvider)
    assert seen == {"info": {"type": "service_account"}, "audience": SERVICE_URL}
