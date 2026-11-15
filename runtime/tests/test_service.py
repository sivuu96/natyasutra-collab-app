"""Warm-instance service: request handling, duplicate protection, and no logging of leases."""

import logging
import threading
from http.server import ThreadingHTTPServer

import httpx
import pytest

from collab_runtime.service import ActiveRuns, make_handler


@pytest.fixture
def server():
    calls: list[tuple[str, str]] = []
    release = threading.Event()
    started = threading.Event()

    def execute(lease: str, execution_name: str) -> int:
        calls.append((lease, execution_name))
        started.set()
        release.wait(5)
        return 0

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(execute, ActiveRuns()))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        yield {"base": base, "calls": calls, "release": release, "started": started}
    finally:
        release.set()
        httpd.shutdown()
        httpd.server_close()


def test_a_run_request_runs_the_invocation_and_returns_its_exit_code(server) -> None:
    server["release"].set()
    response = httpx.post(
        server["base"] + "/runs", json={"execution_name": "service/inv/1", "lease": "lease-1"}, timeout=5
    )
    assert response.status_code == 200
    assert response.json() == {"exit_code": 0}
    assert server["calls"] == [("lease-1", "service/inv/1")]


def test_a_second_start_of_a_running_execution_is_refused(server) -> None:
    results: list[int] = []
    first = threading.Thread(
        target=lambda: results.append(
            httpx.post(server["base"] + "/runs", json={"execution_name": "e", "lease": "a"}, timeout=10).status_code
        )
    )
    first.start()
    assert server["started"].wait(5)
    second = httpx.post(server["base"] + "/runs", json={"execution_name": "e", "lease": "b"}, timeout=5)
    assert second.status_code == 409
    assert second.json() == {"detail": "already_running"}
    server["release"].set()
    first.join(5)
    assert results == [200]
    assert [call[0] for call in server["calls"]] == ["a"]  # the refused request never reached the run


@pytest.mark.parametrize(
    "body",
    [
        {"execution_name": "e"},
        {"lease": "l"},
        {"execution_name": "", "lease": "l"},
        {"execution_name": 5, "lease": "l"},
    ],
)
def test_incomplete_requests_are_rejected(server, body) -> None:
    response = httpx.post(server["base"] + "/runs", json=body, timeout=5)
    assert response.status_code == 400
    assert server["calls"] == []


def test_a_body_that_is_not_json_is_rejected(server) -> None:
    response = httpx.post(server["base"] + "/runs", content=b"not json", timeout=5)
    assert response.status_code == 400


def test_health_and_unknown_paths(server) -> None:
    assert httpx.get(server["base"] + "/healthz", timeout=5).status_code == 200
    assert httpx.get(server["base"] + "/nope", timeout=5).status_code == 404


def test_request_bodies_are_not_logged(server, caplog) -> None:
    server["release"].set()
    with caplog.at_level(logging.INFO, logger="collab.service"):
        httpx.post(server["base"] + "/runs", json={"execution_name": "e", "lease": "secret-lease-value"}, timeout=5)
    assert "secret-lease-value" not in caplog.text
    assert "POST /runs" in caplog.text
