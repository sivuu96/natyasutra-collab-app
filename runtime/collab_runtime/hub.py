"""Client for the hub's internal execution endpoints. Only the run lease or runtime token is ever sent."""

import time
from typing import Any

import httpx

EXCHANGE_PATH = "/api/v1/internal/executions/exchange"
EVENTS_PATH = "/api/v1/internal/executions/events"


class HubError(Exception):
    """The hub refused a request. `code` is the hub's detail code, such as lease_expired."""

    def __init__(self, status_code: int, code: str) -> None:
        super().__init__(f"hub refused: HTTP {status_code} {code}")
        self.status_code = status_code
        self.code = code


class HubClient:
    def __init__(self, base_url: str, *, http: httpx.Client | None = None, retries: int = 3, backoff: float = 2.0):
        self._base = base_url.rstrip("/")
        self._http = http or httpx.Client(timeout=httpx.Timeout(20.0))
        self._retries = retries
        self._backoff = backoff

    def exchange(self, *, lease: str, execution_name: str) -> dict[str, Any]:
        response = self._http.post(
            self._base + EXCHANGE_PATH,
            json={"execution_name": execution_name},
            headers={"Authorization": f"Bearer {lease}"},
        )
        if response.status_code != 200:
            raise HubError(response.status_code, _detail(response))
        return dict(response.json())

    def post_event(self, *, runtime_token: str, event: dict[str, Any]) -> None:
        """Send one event. Transient failures are retried; the sequence number makes retries safe."""
        last: Exception | None = None
        for attempt in range(self._retries):
            try:
                response = self._http.post(
                    self._base + EVENTS_PATH,
                    json=event,
                    headers={"Authorization": f"Bearer {runtime_token}"},
                )
            except httpx.HTTPError as error:
                last = error
            else:
                if response.status_code < 500:
                    if response.status_code >= 400:
                        raise HubError(response.status_code, _detail(response))
                    return
                last = HubError(response.status_code, "hub_unavailable")
            time.sleep(self._backoff * (attempt + 1))
        raise HubError(503, "hub_unreachable") from last


def _detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
    except ValueError:
        return "unknown"
    return detail if isinstance(detail, str) else "unknown"
