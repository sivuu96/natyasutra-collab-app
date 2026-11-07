"""In-process fan-out of committed project events to WebSocket subscribers.

Events are always persisted (ProjectEvent) and committed before `publish` is called, so a
broadcast never announces state that could be lost. This hub is single-process; a multi-instance
deployment needs a shared bus (documented limitation).
"""

import asyncio
import logging
import uuid
from typing import Any

logger = logging.getLogger("app.realtime")


class ProjectHub:
    def __init__(self) -> None:
        self._subscribers: dict[uuid.UUID, set[asyncio.Queue[dict[str, Any]]]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self, project_id: uuid.UUID) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=500)
        self._subscribers.setdefault(project_id, set()).add(queue)
        return queue

    def unsubscribe(self, project_id: uuid.UUID, queue: asyncio.Queue[dict[str, Any]]) -> None:
        subscribers = self._subscribers.get(project_id)
        if subscribers is None:
            return
        subscribers.discard(queue)
        if not subscribers:
            self._subscribers.pop(project_id, None)

    def publish(self, project_id: uuid.UUID, envelope: dict[str, Any]) -> None:
        """Thread-safe. Safe to call from sync request handlers running in a worker thread."""
        if self._loop is None or self._loop.is_closed():
            return
        self._loop.call_soon_threadsafe(self._fanout, project_id, envelope)

    def _fanout(self, project_id: uuid.UUID, envelope: dict[str, Any]) -> None:
        for queue in list(self._subscribers.get(project_id, ())):
            try:
                queue.put_nowait(envelope)
            except asyncio.QueueFull:
                # Slow consumer: drop it so it reconnects and replays from the database.
                logger.warning("dropping slow realtime subscriber", extra={"project_id": str(project_id)})
                self.unsubscribe(project_id, queue)
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait({"event_type": "realtime.resync_required", "project_id": str(project_id)})


hub = ProjectHub()
