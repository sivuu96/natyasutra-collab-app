"""Authenticated project WebSocket with replay and per-viewer visibility.

Connect: wss://<host>/api/v1/ws/projects/{project_id}?since=<last_sequence_seen>
- Origin must be an allowed frontend origin (protects against cross-site WebSocket hijacking).
- Session cookie must be valid and the user must be a project member, else the socket closes (1008).
- Events with sequence > since are replayed from the database, then live events follow.
- Events about a PRIVATE agent session are delivered only to that session's owner (replay and live).
- Membership is re-checked every 30 s; removed members are disconnected.
"""

import asyncio
import contextlib
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.deps import authenticate
from app.models import ProjectEvent
from app.realtime.hub import hub
from app.services.authz import get_membership
from app.services.events import envelope_for

router = APIRouter(tags=["realtime"])

HEARTBEAT_SECONDS = 25
MEMBERSHIP_RECHECK_SECONDS = 30
CLOSE_POLICY_VIOLATION = 1008
CLOSE_RESYNC_REQUIRED = 4001


def visible_to(envelope: dict[str, Any], viewer_id: uuid.UUID) -> bool:
    """Private agent-session events go only to their owner. Everything else is project-visible."""
    if envelope.get("visibility") == "private":
        return envelope.get("owner_user_id") == str(viewer_id)
    return True


def _replay(session_factory, project_id: uuid.UUID, since: int, viewer_id: uuid.UUID) -> list[dict]:  # type: ignore[no-untyped-def]
    with session_factory() as db:
        rows = db.scalars(
            select(ProjectEvent)
            .where(ProjectEvent.project_id == project_id, ProjectEvent.sequence > since)
            .order_by(ProjectEvent.sequence)
            .limit(1000)
        ).all()
        return [envelope for envelope in (envelope_for(row) for row in rows) if visible_to(envelope, viewer_id)]


def _viewer(session_factory, websocket: WebSocket, project_id: uuid.UUID) -> uuid.UUID | None:  # type: ignore[no-untyped-def]
    """The authenticated member's user id, or None if the socket must not be served."""
    with session_factory() as db:
        try:
            user = authenticate(websocket, db)
        except HTTPException:
            return None
        return user.id if get_membership(db, user, project_id) is not None else None


def _still_member(session_factory, websocket: WebSocket, project_id: uuid.UUID) -> bool:  # type: ignore[no-untyped-def]
    return _viewer(session_factory, websocket, project_id) is not None


@router.websocket("/ws/projects/{project_id}")
async def project_socket(websocket: WebSocket, project_id: uuid.UUID, since: int = Query(default=0, ge=0)) -> None:
    settings = websocket.app.state.settings
    origin = websocket.headers.get("origin")
    if origin not in settings.cors_origins:
        await websocket.close(code=CLOSE_POLICY_VIOLATION)
        return

    session_factory = websocket.app.state.session_factory
    viewer_id = await run_in_threadpool(_viewer, session_factory, websocket, project_id)
    if viewer_id is None:
        await websocket.close(code=CLOSE_POLICY_VIOLATION)
        return

    hub.bind_loop(asyncio.get_running_loop())
    await websocket.accept()
    queue = hub.subscribe(project_id)
    last_sent = since
    try:
        for envelope in await run_in_threadpool(_replay, session_factory, project_id, since, viewer_id):
            await websocket.send_json(envelope)
            last_sent = envelope["sequence"]

        async def _drain_client() -> None:
            # Clients send nothing; this loop exists so a disconnect is noticed promptly.
            while True:
                await websocket.receive_text()

        reader = asyncio.create_task(_drain_client())
        last_check = asyncio.get_running_loop().time()
        try:
            while True:
                try:
                    envelope = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except TimeoutError:
                    await websocket.send_json({"event_type": "realtime.heartbeat"})
                    envelope = None

                now = asyncio.get_running_loop().time()
                if now - last_check >= MEMBERSHIP_RECHECK_SECONDS:
                    last_check = now
                    if not await run_in_threadpool(_still_member, session_factory, websocket, project_id):
                        await websocket.close(code=CLOSE_POLICY_VIOLATION)
                        return

                if envelope is None:
                    continue
                if envelope.get("event_type") == "realtime.resync_required":
                    await websocket.close(code=CLOSE_RESYNC_REQUIRED)
                    return
                if not visible_to(envelope, viewer_id):
                    continue
                sequence = envelope.get("sequence")
                if sequence is None:
                    # Unsequenced, non-persisted events (e.g. agent output deltas) always pass through.
                    await websocket.send_json(envelope)
                    continue
                if sequence <= last_sent:
                    continue  # duplicate or already replayed
                await websocket.send_json(envelope)
                last_sent = sequence
                if reader.done():
                    return
        finally:
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                await reader
    except (WebSocketDisconnect, RuntimeError):
        # RuntimeError: send attempted on an already-closed connection.
        pass
    finally:
        hub.unsubscribe(project_id, queue)
