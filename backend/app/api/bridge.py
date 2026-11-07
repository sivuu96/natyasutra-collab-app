"""Bridge endpoints: pairing, token exchange and the bridge WebSocket.

The bridge always connects outbound. Authentication is a bearer token only (never cookies), so a
malicious web page cannot ride a logged-in browser session into the bridge channel.
"""

import asyncio
import contextlib
import json
import logging
import secrets
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.websockets import WebSocketDisconnect

from app.core.security import hash_token
from app.core.tokens import (
    InvalidBridgeToken,
    issue_bridge_token,
    new_device_secret,
    normalise_pairing_code,
    verify_bridge_token,
)
from app.db.base import utcnow
from app.deps import as_utc, enforce_rate_limit, get_db
from app.models import BridgeDevice, PairingCode
from app.realtime.bridge_registry import BridgeConnection, bridge_registry
from app.services.agents import (
    FrameRejected,
    Outcome,
    apply_inbound,
    apply_outcome,
    handle_disconnect,
    record_inbound_event,
)
from app.services.audit import record_audit
from app.services.bridge_protocol import ACK, ERROR, FrameError, validate_inbound

logger = logging.getLogger("app.bridge")
router = APIRouter(prefix="/bridge", tags=["bridge"])

MAX_FRAME_BYTES = 1_000_000
CLOSE_POLICY = 1008
CLOSE_REVOKED = 4003


class PairIn(BaseModel):
    code: str = Field(min_length=8, max_length=12)
    device_name: str = Field(min_length=1, max_length=120)


class PairOut(BaseModel):
    device_id: uuid.UUID
    device_secret: str  # shown once; the hub keeps only its hash


class TokenIn(BaseModel):
    device_id: uuid.UUID
    device_secret: str = Field(min_length=20, max_length=200)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


def _secret_matches(device: BridgeDevice, secret: str) -> bool:
    return secrets.compare_digest(device.secret_hash, hash_token(secret))


@router.post("/pair", response_model=PairOut, status_code=status.HTTP_201_CREATED)
def pair(payload: PairIn, request: Request, db: Session = Depends(get_db)) -> PairOut:
    enforce_rate_limit(
        request, f"bridge-pair:ip:{request.client.host if request.client else 'unknown'}", limit=10, window_seconds=3600
    )
    code_hash = hash_token(normalise_pairing_code(payload.code))
    pairing = db.scalar(select(PairingCode).where(PairingCode.code_hash == code_hash))
    now = utcnow()
    if pairing is None or pairing.redeemed_at is not None or as_utc(pairing.expires_at) <= now:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired pairing code")

    secret = new_device_secret()
    device = BridgeDevice(
        owner_user_id=pairing.owner_user_id, name=payload.device_name.strip(), secret_hash=hash_token(secret)
    )
    db.add(device)
    db.flush()
    pairing.redeemed_at = now
    pairing.device_id = device.id
    record_audit(
        db,
        action="device.paired",
        actor_user_id=pairing.owner_user_id,
        target_type="bridge_device",
        target_id=str(device.id),
        request_id=getattr(request.state, "request_id", None),
    )
    db.commit()
    return PairOut(device_id=device.id, device_secret=secret)


@router.post("/token", response_model=TokenOut)
def exchange_token(payload: TokenIn, request: Request, db: Session = Depends(get_db)) -> TokenOut:
    enforce_rate_limit(request, f"bridge-token:device:{payload.device_id}", limit=30, window_seconds=60)
    device = db.get(BridgeDevice, payload.device_id)
    if device is None or device.revoked_at is not None or not _secret_matches(device, payload.device_secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Device authentication failed")
    settings = request.app.state.settings
    token, expires = issue_bridge_token(settings.bridge_token_secret, device.id, settings.bridge_token_minutes)
    device.last_seen_at = utcnow()
    db.commit()
    return TokenOut(access_token=token, expires_in=int((expires - utcnow()).total_seconds()))


# ---- WebSocket -----------------------------------------------------------------------------------


def _device_if_active(session_factory: Any, device_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID] | None:
    with session_factory() as db:
        device = db.get(BridgeDevice, device_id)
        if device is None or device.revoked_at is not None:
            return None
        return device.id, device.owner_user_id


def _touch(session_factory: Any, device_id: uuid.UUID) -> None:
    with session_factory() as db:
        device = db.get(BridgeDevice, device_id)
        if device is not None:
            device.last_seen_at = utcnow()
            db.commit()


def _process_frame(
    session_factory: Any, device_id: uuid.UUID, frame_type: str, event_id: uuid.UUID, payload: Any
) -> tuple[Outcome, bool]:
    """Persist-then-apply one frame in one transaction. Returns (outcome, fresh)."""
    with session_factory() as db:
        device = db.get(BridgeDevice, device_id)
        if device is None or device.revoked_at is not None:
            raise FrameRejected("device revoked")
        device.last_seen_at = utcnow()
        raw = payload.model_dump(mode="json") if payload is not None else {}
        fresh = record_inbound_event(db, device, event_id, frame_type, None, raw)
        if not fresh:
            db.commit()
            return Outcome(), False
        outcome = apply_inbound(db, device, frame_type, payload)
        db.commit()
        return outcome, True


def _disconnect(session_factory: Any, device_id: uuid.UUID) -> Outcome:
    with session_factory() as db:
        device = db.get(BridgeDevice, device_id)
        if device is None:
            return Outcome()
        outcome = handle_disconnect(db, device)
        db.commit()
        return outcome


@router.websocket("/ws")
async def bridge_socket(websocket: WebSocket) -> None:
    session_factory = websocket.app.state.session_factory
    settings = websocket.app.state.settings
    header = websocket.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        await websocket.close(code=CLOSE_POLICY)
        return
    try:
        device_id = verify_bridge_token(settings.bridge_token_secret, header[len("Bearer ") :])
    except InvalidBridgeToken:
        await websocket.close(code=CLOSE_POLICY)
        return

    active = await run_in_threadpool(_device_if_active, session_factory, device_id)
    if active is None:
        await websocket.close(code=CLOSE_REVOKED)
        return
    _, owner_user_id = active

    await websocket.accept()
    loop = asyncio.get_running_loop()
    connection = BridgeConnection(device_id=device_id, owner_user_id=owner_user_id, loop=loop)
    replaced = bridge_registry.register(connection)
    if replaced is not None:
        replaced.closing.set()  # one live socket per device; the newest wins
    # The welcome frame is sent once the bridge's hello has been applied (see apply_inbound), so a welcome
    # means "your agents are registered".
    logger.info("bridge connected", extra={"device_id": str(device_id)})

    async def writer() -> None:
        while True:
            frame = await connection.outbound.get()
            if frame is None:
                return
            await websocket.send_json(frame)

    async def watch_revocation() -> None:
        await connection.closing.wait()
        with contextlib.suppress(RuntimeError):
            await websocket.close(code=CLOSE_REVOKED)

    writer_task = asyncio.create_task(writer())
    watcher_task = asyncio.create_task(watch_revocation())
    heartbeat_timeout = settings.bridge_heartbeat_timeout_seconds
    try:
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=heartbeat_timeout)
            except TimeoutError:
                logger.warning("bridge heartbeat timeout", extra={"device_id": str(device_id)})
                break
            if len(raw) > MAX_FRAME_BYTES:
                await websocket.close(code=CLOSE_POLICY)
                break
            await _handle_raw(session_factory, connection, raw)
    except WebSocketDisconnect:
        pass
    finally:
        watcher_task.cancel()
        was_current = bridge_registry.unregister(connection)
        connection.outbound.put_nowait(None)
        writer_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, RuntimeError):
            await writer_task
        if was_current:
            outcome = await run_in_threadpool(_disconnect, session_factory, device_id)
            await run_in_threadpool(apply_outcome, outcome)
        logger.info("bridge disconnected", extra={"device_id": str(device_id)})


async def _handle_raw(session_factory: Any, connection: BridgeConnection, raw: str) -> None:
    try:
        frame = json.loads(raw)
    except json.JSONDecodeError:
        connection.outbound.put_nowait({"type": ERROR, "payload": {"code": "malformed_json"}})
        return
    if isinstance(frame, dict) and frame.get("type") == "bridge.heartbeat":
        await run_in_threadpool(_touch, session_factory, connection.device_id)
        return
    try:
        frame_type, event_id, payload = validate_inbound(frame)
    except FrameError as error:
        connection.outbound.put_nowait({"type": ERROR, "payload": {"code": "frame_invalid", "detail": str(error)}})
        return
    assert event_id is not None
    try:
        outcome, fresh = await run_in_threadpool(
            _process_frame, session_factory, connection.device_id, frame_type, event_id, payload
        )
    except FrameRejected as rejected:
        # Deterministic rejection: tell the bridge and acknowledge so it does not retry forever.
        connection.outbound.put_nowait(
            {"type": ERROR, "payload": {"code": "frame_rejected", "event_id": str(event_id), "detail": str(rejected)}}
        )
        connection.outbound.put_nowait({"type": ACK, "payload": {"event_id": str(event_id)}})
        return
    await run_in_threadpool(apply_outcome, outcome)
    connection.outbound.put_nowait({"type": ACK, "payload": {"event_id": str(event_id), "duplicate": not fresh}})
