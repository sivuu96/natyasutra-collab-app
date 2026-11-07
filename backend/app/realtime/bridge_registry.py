"""In-process registry of connected bridges (one WebSocket per device).

Frames are queued to each connection's writer task. `send` is safe to call from threadpool workers
(sync request handlers). Presence is per process; see docs/security.md on single-instance limits.
"""

import asyncio
import logging
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("app.bridge")

OUTBOUND_QUEUE_LIMIT = 500


@dataclass
class BridgeConnection:
    device_id: uuid.UUID
    owner_user_id: uuid.UUID
    loop: asyncio.AbstractEventLoop
    outbound: asyncio.Queue[dict[str, Any] | None] = field(
        default_factory=lambda: asyncio.Queue(maxsize=OUTBOUND_QUEUE_LIMIT)
    )
    closing: asyncio.Event = field(default_factory=asyncio.Event)


class BridgeRegistry:
    def __init__(self) -> None:
        self._connections: dict[uuid.UUID, BridgeConnection] = {}
        self._lock = threading.Lock()

    def register(self, connection: BridgeConnection) -> BridgeConnection | None:
        """Register a connection. Returns the replaced connection, if any (the old socket is closed)."""
        with self._lock:
            previous = self._connections.get(connection.device_id)
            self._connections[connection.device_id] = connection
        return previous

    def unregister(self, connection: BridgeConnection) -> bool:
        """Returns True if this connection was the current one (so its disconnect changes presence)."""
        with self._lock:
            if self._connections.get(connection.device_id) is connection:
                del self._connections[connection.device_id]
                return True
            return False

    def is_online(self, device_id: uuid.UUID) -> bool:
        with self._lock:
            return device_id in self._connections

    def online_device_ids(self) -> set[uuid.UUID]:
        with self._lock:
            return set(self._connections)

    def send(self, device_id: uuid.UUID, frame: dict[str, Any]) -> bool:
        """Queue a frame for a device. Thread-safe. Returns False if the device is not connected."""
        with self._lock:
            connection = self._connections.get(device_id)
        if connection is None or connection.loop.is_closed():
            return False

        def _enqueue() -> None:
            try:
                connection.outbound.put_nowait(frame)
            except asyncio.QueueFull:
                logger.warning("bridge outbound queue full; closing connection", extra={"device_id": str(device_id)})
                connection.closing.set()

        connection.loop.call_soon_threadsafe(_enqueue)
        return True

    def disconnect(self, device_id: uuid.UUID) -> None:
        """Ask a device's connection to close (used on revocation). Thread-safe."""
        with self._lock:
            connection = self._connections.get(device_id)
        if connection is None or connection.loop.is_closed():
            return
        connection.loop.call_soon_threadsafe(connection.closing.set)


bridge_registry = BridgeRegistry()
