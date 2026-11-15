"""Cloud Run service execution provider: the hub asks a warm Cloud Run service to run each invocation.

The service keeps at least one instance warm (min-instances=1), so a run does not wait for a container to start.
The service is private: each request carries a Google identity token for the dispatcher service account, which has
the run.invoker role on the service. Cloud Run rejects any other caller before the request reaches the runtime.

A run's request stays open for the whole run, which can last up to the runtime's limit. So the hub sends it from a
background thread and returns at once. A dispatch that cannot reach the service is retried a few times. If every
attempt fails, the sweeper fails the invocation after START_TIMEOUT, as it does for a run that never starts.

Stopping: the hub marks the invocation cancelled, and the run stops at its next heartbeat, which the hub refuses
with run_finished. Nothing is sent to the service.
"""

import base64
import json
import logging
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import service_account

from app.services.execution import ProviderError

log = logging.getLogger(__name__)

DISPATCH_ATTEMPTS = 3
DISPATCH_BACKOFF_SECONDS = 2.0
# The runtime stops a run at 25 minutes. The read timeout must outlast that, because the response comes at the end.
RUN_READ_TIMEOUT_SECONDS = 30 * 60
RETRYABLE_STATUS = {408, 429}


def load_dispatcher_info(encoded_key: str) -> dict[str, Any]:
    """Decode the base64 service-account JSON from GCP_DISPATCHER_KEY. Raises ValueError on a malformed key."""
    try:
        info = json.loads(base64.b64decode(encoded_key, validate=True))
    except (ValueError, json.JSONDecodeError) as error:
        raise ValueError("GCP_DISPATCHER_KEY is not valid base64-encoded service-account JSON") from error
    if not isinstance(info, dict):
        raise ValueError("GCP_DISPATCHER_KEY does not hold a service-account key")
    return info


def id_token_source(info: dict[str, Any], audience: str) -> Callable[[], str]:
    """Return a function that gives a current identity token for the service URL, refreshing it when it expires."""
    credentials = service_account.IDTokenCredentials.from_service_account_info(info, target_audience=audience)

    def token() -> str:
        if not credentials.valid:
            credentials.refresh(GoogleRequest())
        return str(credentials.token)

    return token


class CloudRunServiceProvider:
    def __init__(
        self,
        *,
        service_url: str,
        token_source: Callable[[], str],
        http: httpx.Client | None = None,
        background: bool = True,
    ) -> None:
        self._runs_url = service_url.rstrip("/") + "/runs"
        self._token = token_source
        self._http = http or httpx.Client(
            timeout=httpx.Timeout(connect=20.0, read=RUN_READ_TIMEOUT_SECONDS, write=20.0, pool=20.0)
        )
        # Tests dispatch inline so they can check the outcome. Production dispatches on a pool.
        self._pool = ThreadPoolExecutor(max_workers=16, thread_name_prefix="dispatch") if background else None

    def submit(self, *, invocation_id: uuid.UUID, dedupe_key: str, lease_token: str) -> str:
        """Dispatch a run. Returns the execution name the lease is bound to.

        Signing the request happens here, so a credential failure is raised and the outbox retries it. The HTTP call
        itself happens after this returns.
        """
        try:
            token = self._token()
        except Exception as error:
            raise ProviderError(f"could not sign the service request: {type(error).__name__}") from error
        execution_name = f"service/{invocation_id}/{uuid.uuid4().hex}"
        payload = {"execution_name": execution_name, "lease": lease_token}
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        if self._pool is None:
            self._dispatch(invocation_id, payload, headers)
        else:
            self._pool.submit(self._dispatch, invocation_id, payload, headers)
        return execution_name

    def _dispatch(self, invocation_id: uuid.UUID, payload: dict[str, str], headers: dict[str, str]) -> bool:
        for attempt in range(1, DISPATCH_ATTEMPTS + 1):
            try:
                response = self._http.post(self._runs_url, json=payload, headers=headers)
            except httpx.HTTPError as error:
                reason = type(error).__name__
            else:
                if response.status_code == 200:
                    return True
                if response.status_code == 409:
                    return True  # an earlier attempt reached the service and the run is already going
                if response.status_code < 500 and response.status_code not in RETRYABLE_STATUS:
                    log.error("service refused invocation %s: HTTP %d", invocation_id, response.status_code)
                    return False
                reason = f"HTTP {response.status_code}"
            log.warning("dispatch attempt %d for invocation %s failed: %s", attempt, invocation_id, reason)
            if attempt < DISPATCH_ATTEMPTS:
                time.sleep(DISPATCH_BACKOFF_SECONDS * attempt)
        log.error("giving up on dispatching invocation %s; the sweeper will fail it", invocation_id)
        return False


__all__ = ["CloudRunServiceProvider", "id_token_source", "load_dispatcher_info"]
