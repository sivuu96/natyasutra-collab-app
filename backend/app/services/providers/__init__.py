"""Execution provider selection.

The interface lives in app.services.execution. Production uses the Cloud Run service provider; the fake provider
serves tests and local development.
"""

from app.core.config import Settings
from app.services.execution import ExecutionProvider, FakeExecutionProvider
from app.services.providers.cloud_run_service import (
    CloudRunServiceProvider,
    id_token_source,
    load_dispatcher_info,
)


def build_execution_provider(settings: Settings) -> ExecutionProvider:
    """Return the provider for the configured mode."""
    if settings.execution_provider == "fake":
        return FakeExecutionProvider()
    assert settings.gcp_dispatcher_key is not None and settings.runtime_service_url
    info = load_dispatcher_info(settings.gcp_dispatcher_key.get_secret_value())
    return CloudRunServiceProvider(
        service_url=settings.runtime_service_url,
        token_source=id_token_source(info, settings.runtime_service_url),
    )


__all__ = ["CloudRunServiceProvider", "build_execution_provider"]
