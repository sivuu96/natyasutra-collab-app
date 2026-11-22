"""A local proxy that holds the Anthropic key, so the Claude Code process never has it.

The CLI is pointed at this proxy (ANTHROPIC_BASE_URL). The proxy adds the real key to each request, forwards it to
the Anthropic API, and streams the reply back. Only the runtime process holds the key, and the agent's tools cannot
read it: its environment contains a placeholder, not the key.

The proxy listens on 127.0.0.1 on a random port, serves one run, and stops when the run ends. It forwards only
paths under /v1/, so it cannot be used to reach anything else.
"""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Self

import httpx

UPSTREAM_URL = "https://api.anthropic.com"
# Headers the proxy does not pass on. The key is replaced, and the rest are handled by the proxy itself.
DROP_REQUEST = {
    "host",
    "content-length",
    "connection",
    "authorization",
    "x-api-key",
    "accept-encoding",
    "transfer-encoding",
}
DROP_RESPONSE = {
    "content-length",
    "transfer-encoding",
    "connection",
    "content-encoding",
}


class ModelProxy:
    def __init__(
        self,
        api_key: str,
        *,
        upstream: str = UPSTREAM_URL,
        http: httpx.Client | None = None,
    ) -> None:
        self._key = api_key
        self._upstream = upstream.rstrip("/")
        self._http = http or httpx.Client(timeout=httpx.Timeout(600.0, connect=10.0))
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="model-proxy", daemon=True
        )

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def __enter__(self) -> Self:
        self._thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._key = ""  # the proxy forgets the key once the run is over

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = (
                "HTTP/1.0"  # one request per connection keeps streamed replies simple
            )

            def do_GET(self) -> None:
                proxy._forward(self, "GET")

            def do_POST(self) -> None:
                proxy._forward(self, "POST")

            def log_message(self, format: str, *args: object) -> None:
                return  # request lines carry no secrets, but nothing here needs logging

        return Handler

    def _forward(self, handler: BaseHTTPRequestHandler, method: str) -> None:
        # The body is read first, so the client always gets a complete answer, even for a refused path.
        length = int(handler.headers.get("Content-Length", "0") or 0)
        body = handler.rfile.read(length) if length else b""
        if not handler.path.startswith("/v1/"):
            handler.send_error(404)
            return
        headers = {
            name: value
            for name, value in handler.headers.items()
            if name.lower() not in DROP_REQUEST
        }
        headers["x-api-key"] = self._key
        # The reply is passed on without decoding, and its content-encoding header is dropped. So ask for plain text;
        # otherwise httpx's default (gzip) would send compressed bytes that the CLI reads as garbage.
        headers["accept-encoding"] = "identity"
        try:
            with self._http.stream(
                method, self._upstream + handler.path, headers=headers, content=body
            ) as upstream:
                handler.send_response(upstream.status_code)
                for name, value in upstream.headers.items():
                    if name.lower() not in DROP_RESPONSE:
                        handler.send_header(name, value)
                handler.send_header("Connection", "close")
                handler.end_headers()
                for chunk in upstream.iter_raw():
                    handler.wfile.write(chunk)
                    handler.wfile.flush()
        except httpx.HTTPError:
            # Nothing about the error is passed on: it could contain the request, and so the key path.
            handler.send_error(502, "model service unavailable")


__all__ = ["DROP_REQUEST", "UPSTREAM_URL", "ModelProxy"]
