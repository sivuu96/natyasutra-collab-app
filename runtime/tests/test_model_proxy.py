"""The model proxy: it adds the key, forwards only /v1/ paths, streams the reply back, and stops with the run."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx
import pytest

from collab_runtime.model_proxy import ModelProxy

KEY = "sk-ant-api03-PROXYKEY-98765432"


class Upstream:
    """Stands in for the Anthropic API. Records what it was sent, and answers with a streamed body."""

    def __init__(self) -> None:
        self.seen: list[dict[str, Any]] = []
        seen = self.seen

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                seen.append(
                    {
                        "path": self.path,
                        "headers": dict(self.headers),
                        "body": self.rfile.read(length),
                    }
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("request-id", "req_test")
                self.end_headers()
                for part in (b"event: message_start\n", b"data: {}\n\n"):
                    self.wfile.write(part)
                    self.wfile.flush()

            def log_message(self, format: str, *args: object) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def upstream() -> Any:
    server = Upstream()
    yield server
    server.close()


def test_the_proxy_adds_the_key_and_streams_the_reply(upstream: Upstream) -> None:
    with ModelProxy(KEY, upstream=upstream.url) as proxy:
        response = httpx.post(
            f"{proxy.url}/v1/messages",
            json={"model": "claude-haiku-5-5", "stream": True},
            headers={
                "x-api-key": "held-by-runtime",
                "authorization": "Bearer cli-own-token",
                "anthropic-version": "2023-06-01",
            },
        )

    assert response.status_code == 200
    assert response.text == "event: message_start\ndata: {}\n\n"
    sent = upstream.seen[0]
    assert sent["path"] == "/v1/messages"
    assert sent["headers"]["x-api-key"] == KEY  # the real key was added by the proxy
    assert "authorization" not in {
        name.lower() for name in sent["headers"]
    }  # the CLI's own token is not passed on
    assert sent["headers"]["anthropic-version"] == "2023-06-01"
    assert b"claude-haiku-5-5" in sent["body"]


def test_the_key_is_not_sent_back_to_the_client(upstream: Upstream) -> None:
    with ModelProxy(KEY, upstream=upstream.url) as proxy:
        response = httpx.post(f"{proxy.url}/v1/messages", json={"stream": True})
    assert KEY not in response.text
    assert all(KEY not in value for value in response.headers.values())


def test_only_v1_paths_are_forwarded(upstream: Upstream) -> None:
    with ModelProxy(KEY, upstream=upstream.url) as proxy:
        response = httpx.post(f"{proxy.url}/admin/anything", json={})
    assert response.status_code == 404
    assert upstream.seen == []


def test_an_unreachable_upstream_gives_a_plain_error_without_details(
    upstream: Upstream,
) -> None:
    upstream.close()  # nothing is listening any more
    with ModelProxy(KEY, upstream=upstream.url) as proxy:
        response = httpx.post(f"{proxy.url}/v1/messages", json={})
    assert response.status_code == 502
    assert KEY not in response.text


def test_the_proxy_stops_when_the_run_ends(upstream: Upstream) -> None:
    with ModelProxy(KEY, upstream=upstream.url) as proxy:
        url = proxy.url
    with pytest.raises(httpx.TransportError):  # refused, or timed out on some platforms
        httpx.post(f"{url}/v1/messages", json={}, timeout=2)
