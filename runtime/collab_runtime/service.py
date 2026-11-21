"""Warm-instance mode: an HTTP service that runs agent invocations when the hub asks.

The hub calls `POST /runs` with a Google identity token (Cloud Run checks that token before the request reaches
this code) and the lease for one invocation. The request stays open for the whole run and returns when the run
ends, so the instance counts as busy until then. The service runs at concurrency 1, so a run never shares an
instance. Each run reports to the hub with the same events and checks.

Endpoints:
- POST /runs with {"execution_name": str, "lease": str} -> 200 {"exit_code": n} when the run ends.
  409 {"detail": "already_running"} if the same execution is already running here.
- GET /healthz -> 200.
"""

import json
import logging
import os
import sys
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

MAX_BODY_BYTES = 16_384

log = logging.getLogger("collab.service")


class ActiveRuns:
    """Remembers which executions are running on this instance, so a retried dispatch cannot start one twice."""

    def __init__(self) -> None:
        self._names: set[str] = set()
        self._lock = threading.Lock()

    def claim(self, execution_name: str) -> bool:
        with self._lock:
            if execution_name in self._names:
                return False
            self._names.add(execution_name)
            return True

    def release(self, execution_name: str) -> None:
        with self._lock:
            self._names.discard(execution_name)


def make_handler(execute: Callable[[str, str], int], active: ActiveRuns) -> type[BaseHTTPRequestHandler]:
    """Build the request handler. `execute(lease, execution_name)` runs one invocation and returns its exit code."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/healthz":
                self._send(200, {"status": "ok"})
            else:
                self._send(404, {"detail": "not_found"})

        def do_POST(self) -> None:
            if self.path != "/runs":
                self._send(404, {"detail": "not_found"})
                return
            body = self._read_body()
            if body is None:
                self._send(400, {"detail": "invalid_request"})
                return
            lease = body.get("lease")
            execution_name = body.get("execution_name")
            if not isinstance(lease, str) or not lease or not isinstance(execution_name, str) or not execution_name:
                self._send(400, {"detail": "invalid_request"})
                return
            if not active.claim(execution_name):
                self._send(409, {"detail": "already_running"})
                return
            try:
                exit_code = execute(lease, execution_name)
            finally:
                active.release(execution_name)
                lease = ""  # the lease is single use; drop the reference
            self._send(200, {"exit_code": exit_code})

        def _read_body(self) -> dict[str, Any] | None:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return None
            if length <= 0 or length > MAX_BODY_BYTES:
                return None
            try:
                parsed = json.loads(self.rfile.read(length))
            except ValueError:
                return None
            return parsed if isinstance(parsed, dict) else None

        def _send(self, status: int, payload: dict[str, Any]) -> None:
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: object) -> None:
            # Method and path only. Request bodies carry leases and are never logged.
            log.info("%s %s", self.command, self.path)

    return Handler


def serve() -> int:
    """Start the service on $PORT (Cloud Run sets it). Returns only if the server stops."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s", stream=sys.stderr)
    import httpx

    from collab_runtime.harness import ClaudeRunner
    from collab_runtime.hub import HubClient
    from collab_runtime.main import Config, make_repo_preparer, make_write_token, run
    from collab_runtime.secrets import read_secret

    try:
        config = Config.from_env(dict(os.environ))
    except KeyError as error:
        log.error("missing configuration: %s", error.args[0])
        return 2

    hub = HubClient(config.hub_base_url)
    prepare_repo = make_repo_preparer(config, read_secret, httpx.Client(timeout=httpx.Timeout(20.0)))
    write_token = make_write_token(config, read_secret, httpx.Client(timeout=httpx.Timeout(20.0)))

    def execute(lease: str, execution_name: str) -> int:
        return run(
            config,
            hub=hub,
            read_secret=read_secret,
            runner=ClaudeRunner(timeout_seconds=config.timeout_seconds),
            prepare_repo=prepare_repo,
            write_token=write_token,
            lease=lease,
            execution_name=execution_name,
        )

    port = int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), make_handler(execute, ActiveRuns()))
    log.info("runtime service listening on port %d", port)
    server.serve_forever()
    return 0
