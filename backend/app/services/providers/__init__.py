"""Execution provider implementations and the factory that selects one from settings.

The interface lives in app.services.execution.
"""

from app.core.config import Settings
from app.services.execution import ExecutionProvider, FakeExecutionProvider
from app.services.providers.cloud_run import CloudRunJobsProvider, load_dispatcher_credentials, load_dispatcher_info
from app.services.providers.cloud_run_service import CloudRunServiceProvider, id_token_source


def build_execution_provider(settings: Settings) -> ExecutionProvider:
    """Return the provider for the configured mode: the in-process fake (tests, local runs) or Cloud Run Jobs."""
    if settings.execution_provider == "fake":
        return FakeExecutionProvider()
    if settings.execution_provider == "cloud_run_service":
        assert settings.gcp_dispatcher_key is not None and settings.runtime_service_url
        info = load_dispatcher_info(settings.gcp_dispatcher_key.get_secret_value())
        return CloudRunServiceProvider(
            service_url=settings.runtime_service_url,
            token_source=id_token_source(info, settings.runtime_service_url),
        )
    assert settings.gcp_dispatcher_key is not None and settings.gcp_project_id and settings.hub_base_url
    credentials = load_dispatcher_credentials(settings.gcp_dispatcher_key.get_secret_value())
    return CloudRunJobsProvider(
        project_id=settings.gcp_project_id,
        region=settings.gcp_region,
        job_name=settings.gcp_job_name,
        hub_base_url=settings.hub_base_url,
        credentials=credentials,
    )


__all__ = ["CloudRunJobsProvider", "CloudRunServiceProvider", "build_execution_provider", "load_dispatcher_credentials"]
