"""Outbound connection to the hub: authenticate, register agents, stream frames, reconnect.

- Connects outbound only (no listening port on this machine).
- Exchanges the device secret for a 15-minute access token before each connection.
- Frames the bridge must not lose go through the local outbox and are replayed until acknowledged.
- Heartbeats every 25 seconds. The hub drops the connection after 60 seconds of silence.
- Reconnects with exponential backoff and full jitter. A revoked or unauthorised device stops retrying.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import Any

import httpx
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from collab_bridge import protocol as p
from collab_bridge.adapters.base import AgentAdapter
from collab_bridge.config import BridgeConfig
from collab_bridge.runner import InvocationRunner
from collab_bridge.state import BridgeState

logger = logging.getLogger("collab_bridge.client")

BRIDGE_VERSION = "0.1.0"
HEARTBEAT_SECONDS = 25
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_CAP_SECONDS = 60.0
CLOSE_REVOKED = 4003
CLOSE_POLICY = 1008


class AuthRevoked(Exception):
    """The hub refused this device. Retrying cannot help; the user must re-pair or check revocation."""


def backoff_delay(attempt: int) -> float:
    """Full jitter: a random delay between 0 and the capped exponential ceiling."""
    ceiling = min(BACKOFF_CAP_SECONDS, BACKOFF_BASE_SECONDS * (2 ** min(attempt, 10)))
    return max(0.2, random.uniform(0, ceiling))


def websocket_url(hub_url: str) -> str:
    base = hub_url.rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://") :]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://") :]
    return f"{base}/api/v1/bridge/ws"


class BridgeClient:
    def __init__(
        self,
        config: BridgeConfig,
        device_id: str,
        device_secret: str,
        state: BridgeState,
        adapters: dict[str, AgentAdapter],
    ) -> None:
        self._config = config
        self._device_id = device_id
        self._device_secret = device_secret
        self._state = state
        self._adapters = adapters
        self._send_queue: asyncio.Queue[dict[str, Any] | None] | None = None
        self._available = {
            agent.harness
            for agent in config.agents
            if agent.harness in adapters and adapters[agent.harness].discover(agent.harness_bin).available
        }
        self._runner = InvocationRunner(config, adapters, self._available, self.emit)

    # ---- outbound ----------------------------------------------------------------------------

    def emit(self, frame_type: str, payload: dict[str, Any]) -> None:
        """Queue a frame. Outbox types are persisted first, so a crash or disconnect cannot lose them."""
        frame = p.envelope(frame_type, payload)
        if frame_type in p.OUTBOX_TYPES:
            self._state.enqueue(frame)
        if self._send_queue is not None:
            self._send_queue.put_nowait(frame)

    # ---- connection loop ---------------------------------------------------------------------

    async def run_forever(self) -> None:
        attempt = 0
        while True:
            try:
                await self._session()
                attempt = 0
                await asyncio.sleep(1.0)  # a clean close from the hub must not turn into a tight reconnect loop
            except AuthRevoked as revoked:
                logger.error("hub refused this device: %s", revoked)
                return
            except (OSError, ConnectionClosed, TimeoutError, httpx.HTTPError) as error:
                delay = backoff_delay(attempt)
                attempt += 1
                logger.warning("disconnected (%s); retrying in %.1fs", type(error).__name__, delay)
                await asyncio.sleep(delay)

    async def _exchange_token(self) -> str:
        async with httpx.AsyncClient(base_url=self._config.hub_url, timeout=15) as http:
            response = await http.post(
                "/api/v1/bridge/token", json={"device_id": self._device_id, "device_secret": self._device_secret}
            )
        if response.status_code == 401:
            raise AuthRevoked("device authentication failed (revoked, or secret no longer valid)")
        response.raise_for_status()
        return str(response.json()["access_token"])

    async def _session(self) -> None:
        token = await self._exchange_token()
        url = websocket_url(self._config.hub_url)
        async with connect(
            url, additional_headers={"Authorization": f"Bearer {token}"}, max_size=1_000_000, open_timeout=20
        ) as ws:
            logger.info("connected to hub")
            self._send_queue = asyncio.Queue()
            sender = asyncio.create_task(self._sender(ws))
            heartbeat = asyncio.create_task(self._heartbeat())
            try:
                await ws.send(json.dumps(self._hello()))
                for frame in self._state.pending():  # replay unacknowledged outbox, oldest first
                    self._send_queue.put_nowait(frame)
                async for raw in ws:
                    await self._handle(raw)
            except ConnectionClosed as closed:
                if closed.rcvd is not None and closed.rcvd.code == CLOSE_REVOKED:
                    raise AuthRevoked("this device was revoked in the hub") from closed
                if closed.rcvd is not None and closed.rcvd.code == CLOSE_POLICY:
                    raise AuthRevoked("the hub rejected the connection (policy)") from closed
                raise
            finally:
                heartbeat.cancel()
                sender.cancel()
                self._send_queue = None

    def _hello(self) -> dict[str, Any]:
        agents = [
            p.AgentDescriptor(
                handle=a.handle,
                display_name=a.display_name,
                harness=a.harness,  # type: ignore[arg-type]
                description=a.description,
                capabilities=list(a.capabilities),
            )
            for a in self._config.agents
            if a.harness in self._available
        ]
        skipped = [a.handle for a in self._config.agents if a.harness not in self._available]
        if skipped:
            logger.warning("not registering agents whose harness is unavailable: %s", ", ".join(skipped))
        return p.hello_frame(agents, BRIDGE_VERSION)

    async def _sender(self, ws: ClientConnection) -> None:
        assert self._send_queue is not None
        queue = self._send_queue
        while True:
            frame = await queue.get()
            if frame is None:
                return
            await ws.send(json.dumps(frame))

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            if self._send_queue is not None:
                self._send_queue.put_nowait(p.heartbeat_frame())

    # ---- inbound -----------------------------------------------------------------------------

    async def _handle(self, raw: str | bytes) -> None:
        try:
            frame = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning("ignoring malformed frame from hub")
            return
        frame_type = frame.get("type")
        payload = frame.get("payload") or {}

        if frame_type == p.ACK:
            event_id = payload.get("event_id")
            if event_id:
                self._state.acknowledge(str(event_id))
        elif frame_type == p.WELCOME:
            logger.info("hub protocol %s accepted", payload.get("protocol_version"))
        elif frame_type == p.ERROR:
            code = payload.get("code")
            logger.warning("hub reported %s: %s", code, payload.get("detail", ""))
            if code == "frame_rejected" and payload.get("event_id"):
                self._state.acknowledge(str(payload["event_id"]))
        elif frame_type == p.INVOCATION_CREATED:
            self._runner.handle_created(payload)
        elif frame_type == p.INVOCATION_CANCEL:
            await self._runner.cancel(str(payload.get("invocation_id", "")))
        elif frame_type == p.APPROVAL_RESOLVED:
            self._runner.resolve_approval(str(payload.get("invocation_id", "")), str(payload.get("decision", "")))
        else:
            logger.warning("ignoring unknown frame type from hub: %s", frame_type)
