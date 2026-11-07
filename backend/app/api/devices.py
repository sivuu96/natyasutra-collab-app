"""User-facing device management: pairing codes, device list and revocation, and the user's agents."""

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_token
from app.core.tokens import new_pairing_code
from app.db.base import utcnow
from app.deps import enforce_rate_limit, get_current_user, get_db
from app.models import Agent, BridgeDevice, PairingCode, User
from app.models.enums import AuditOutcome
from app.realtime.bridge_registry import bridge_registry
from app.services.audit import record_audit

router = APIRouter(tags=["devices"])


class PairingCodeOut(BaseModel):
    code: str
    expires_at: dt.datetime


class DeviceOut(BaseModel):
    id: uuid.UUID
    name: str
    created_at: dt.datetime
    last_seen_at: dt.datetime | None
    revoked_at: dt.datetime | None
    online: bool


class MyAgentOut(BaseModel):
    id: uuid.UUID
    handle: str
    display_name: str
    harness: str
    description: str | None
    device_id: uuid.UUID
    device_name: str
    online: bool
    revoked: bool


@router.post("/devices/pairing-codes", response_model=PairingCodeOut, status_code=status.HTTP_201_CREATED)
def create_pairing_code(
    request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> PairingCodeOut:
    enforce_rate_limit(request, f"pairing-codes:user:{user.id}", limit=20, window_seconds=3600)
    settings = request.app.state.settings
    code = new_pairing_code()
    expires = utcnow() + dt.timedelta(minutes=settings.pairing_code_minutes)
    db.add(PairingCode(owner_user_id=user.id, code_hash=hash_token(code.replace("-", "")), expires_at=expires))
    record_audit(
        db,
        action="device.pairing_code_created",
        actor_user_id=user.id,
        request_id=getattr(request.state, "request_id", None),
    )
    db.commit()
    return PairingCodeOut(code=code, expires_at=expires)


@router.get("/devices", response_model=list[DeviceOut])
def list_devices(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[DeviceOut]:
    devices = db.scalars(
        select(BridgeDevice).where(BridgeDevice.owner_user_id == user.id).order_by(BridgeDevice.created_at.desc())
    ).all()
    return [
        DeviceOut(
            id=d.id,
            name=d.name,
            created_at=d.created_at,
            last_seen_at=d.last_seen_at,
            revoked_at=d.revoked_at,
            online=d.revoked_at is None and bridge_registry.is_online(d.id),
        )
        for d in devices
    ]


@router.delete("/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_device(
    device_id: uuid.UUID, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> None:
    device = db.scalar(select(BridgeDevice).where(BridgeDevice.id == device_id, BridgeDevice.owner_user_id == user.id))
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if device.revoked_at is None:
        device.revoked_at = utcnow()
        for agent in db.scalars(select(Agent).where(Agent.device_id == device.id)).all():
            agent.revoked_at = utcnow()
        record_audit(
            db,
            action="device.revoked",
            actor_user_id=user.id,
            target_type="bridge_device",
            target_id=str(device.id),
            outcome=AuditOutcome.SUCCESS,
            request_id=getattr(request.state, "request_id", None),
        )
        db.commit()
    # Cut the live connection immediately; the socket's own cleanup fails any active invocations.
    bridge_registry.disconnect(device.id)


@router.get("/me/agents", response_model=list[MyAgentOut])
def my_agents(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[MyAgentOut]:
    rows = db.execute(
        select(Agent, BridgeDevice)
        .join(BridgeDevice, BridgeDevice.id == Agent.device_id)
        .where(Agent.owner_user_id == user.id)
        .order_by(Agent.handle)
    ).all()
    return [
        MyAgentOut(
            id=a.id,
            handle=a.handle,
            display_name=a.display_name,
            harness=a.harness,
            description=a.description,
            device_id=d.id,
            device_name=d.name,
            online=a.revoked_at is None and d.revoked_at is None and bridge_registry.is_online(d.id),
            revoked=a.revoked_at is not None or d.revoked_at is not None,
        )
        for a, d in rows
    ]
