"""Helpers that act as a local bridge against the hub's HTTP and WebSocket API."""

import uuid
from typing import Any

from fastapi.testclient import TestClient
from helpers import API, csrf_headers

BEARER_PREFIX = "Bearer "


def pair_device(owner: TestClient, bridge: TestClient, name: str = "Laptop") -> tuple[str, str]:
    """Owner creates a one-time code; the bridge redeems it. Returns (device_id, device_secret)."""
    code = owner.post(f"{API}/devices/pairing-codes", headers=csrf_headers(owner))
    assert code.status_code == 201, code.text
    response = bridge.post(f"{API}/bridge/pair", json={"code": code.json()["code"], "device_name": name})
    assert response.status_code == 201, response.text
    body = response.json()
    return str(body["device_id"]), str(body["device_secret"])


def bridge_token(bridge: TestClient, device_id: str, secret: str) -> str:
    response = bridge.post(f"{API}/bridge/token", json={"device_id": device_id, "device_secret": secret})
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


def frame(frame_type: str, payload: dict[str, Any] | None = None, event_id: str | None = None) -> dict[str, Any]:
    return {"type": frame_type, "event_id": event_id or str(uuid.uuid4()), "payload": payload or {}}


def hello(*agents: tuple[str, str, str]) -> dict[str, Any]:
    """agents: (handle, display_name, harness)."""
    return frame(
        "bridge.hello",
        {
            "bridge_version": "test",
            "agents": [{"handle": h, "display_name": d, "harness": k, "capabilities": ["chat"]} for h, d, k in agents],
        },
    )


def read_until(ws: Any, frame_type: str, limit: int = 20) -> dict[str, Any]:
    """Read frames until one of the given type arrives (skips welcome/acks/heartbeats)."""
    for _ in range(limit):
        received = ws.receive_json()
        if received.get("type") == frame_type:
            return received
    raise AssertionError(f"did not receive {frame_type}")


def bearer(token: str) -> dict[str, str]:
    return {"authorization": f"{BEARER_PREFIX}{token}"}
