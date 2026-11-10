"""Cloud Run Jobs execution provider.

Starts one job execution per invocation attempt with `jobs:run`, passing only identifiers in the environment
overrides. No credentials are passed: the runtime obtains its own short-lived credentials (see the runtime
spec). The hub authenticates as the dispatcher service account using the key in GCP_DISPATCHER_KEY.

Limitation: `jobs:run` has no idempotency key. A timed-out call can start a second execution. The runtime's
one-use lease exchange is what prevents the second execution from doing work.
"""

import base64
import json
import uuid
from typing import Any

import httpx
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import service_account

from app.services.execution import ProviderError

RUN_API = "https://run.googleapis.com/v2"
SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


def load_dispatcher_credentials(encoded_key: str) -> Any:
    """Decode the base64 service-account JSON. Raises ValueError on a malformed key."""
    try:
        info = json.loads(base64.b64decode(encoded_key, validate=True))
    except (ValueError, json.JSONDecodeError) as error:
        raise ValueError("GCP_DISPATCHER_KEY is not valid base64-encoded service-account JSON") from error
    return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)


class CloudRunJobsProvider:
    def __init__(
        self,
        *,
        project_id: str,
        region: str,
        job_name: str,
        hub_base_url: str,
        credentials: Any,
        http: httpx.Client | None = None,
    ) -> None:
        self._job = f"projects/{project_id}/locations/{region}/jobs/{job_name}"
        self._hub_base_url = hub_base_url.rstrip("/")
        self._credentials = credentials
        self._http = http or httpx.Client(timeout=httpx.Timeout(20.0))

    def _headers(self) -> dict[str, str]:
        if not self._credentials.valid:
            self._credentials.refresh(GoogleRequest())
        return {"Authorization": f"Bearer {self._credentials.token}", "Content-Type": "application/json"}

    def submit(self, *, invocation_id: uuid.UUID, dedupe_key: str) -> str:
        """Start a job execution. Returns the execution resource name."""
        body = {
            "overrides": {
                "containerOverrides": [
                    {
                        "env": [
                            {"name": "INVOCATION_ID", "value": str(invocation_id)},
                            {"name": "DEDUPE_KEY", "value": dedupe_key},
                            {"name": "HUB_BASE_URL", "value": self._hub_base_url},
                        ]
                    }
                ]
            }
        }
        try:
            response = self._http.post(f"{RUN_API}/{self._job}:run", json=body, headers=self._headers())
        except httpx.HTTPError as error:
            raise ProviderError(f"jobs.run transport error: {type(error).__name__}") from error
        if response.status_code >= 400:
            raise ProviderError(f"jobs.run returned HTTP {response.status_code}: {response.text[:200]}")
        operation = response.json()
        execution_name = (operation.get("metadata") or {}).get("name")
        if not execution_name:
            raise ProviderError("jobs.run response did not include an execution name")
        return execution_name

    def cancel(self, provider_execution_id: str) -> None:
        """Cancel an execution. An execution that no longer exists counts as already cancelled."""
        try:
            response = self._http.post(f"{RUN_API}/{provider_execution_id}:cancel", headers=self._headers(), json={})
        except httpx.HTTPError as error:
            raise ProviderError(f"cancel transport error: {type(error).__name__}") from error
        if response.status_code == 404:
            return
        if response.status_code >= 400:
            raise ProviderError(f"cancel returned HTTP {response.status_code}")


__all__ = ["CloudRunJobsProvider", "load_dispatcher_credentials"]
