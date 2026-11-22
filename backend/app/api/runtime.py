"""Internal endpoints for agent runs. Authenticated by the one-use run lease, never by a browser session."""

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.deps import get_db
from app.realtime.hub import hub
from app.services.leases import LeaseError, exchange_lease, verify_runtime_token
from app.services.runtime_events import RunEventError, RunEventIn, apply_runtime_event

router = APIRouter(prefix="/internal/executions", tags=["runtime"])


class ExchangeIn(BaseModel):
    execution_name: str = Field(min_length=1, max_length=300)


class ExchangeOut(BaseModel):
    invocation_id: str
    prompt: str
    agent_handle: str
    harness: str
    runtime_token: str
    runtime_expires_in: int
    repository: str | None = None
    ref: str | None = None
    can_write: bool = False
    push_requested: bool = False
    message: str = ""
    session_id: str | None = None


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_lease")
    token = authorization[len("Bearer ") :].strip()
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_lease")
    return token


@router.post("/exchange", response_model=ExchangeOut)
def exchange(
    payload: ExchangeIn,
    request: Request,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> ExchangeOut:
    """Trade a one-use run lease for the run context and a 15-minute runtime token."""
    try:
        context = exchange_lease(
            db,
            lease_token=_bearer(authorization),
            execution_name=payload.execution_name,
            runtime_secret=request.app.state.settings.runtime_token_secret,
            allowed_repos=frozenset(request.app.state.settings.github_allowed_repos),
        )
    except LeaseError as error:
        raise HTTPException(status_code=error.status_code, detail=error.code) from error
    return ExchangeOut(
        invocation_id=str(context.invocation_id),
        prompt=context.prompt,
        agent_handle=context.agent_handle,
        harness=context.harness,
        runtime_token=context.runtime_token,
        runtime_expires_in=context.runtime_expires_in,
        repository=context.repository,
        ref=context.ref,
        can_write=context.can_write,
        push_requested=context.push_requested,
        message=context.message,
        session_id=context.session_id,
    )


@router.post("/events")
def post_event(
    payload: RunEventIn,
    request: Request,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    """Report progress for a run. The runtime token names the run; a repeated sequence number is a no-op."""
    try:
        invocation_id = verify_runtime_token(request.app.state.settings.runtime_token_secret, _bearer(authorization))
    except jwt.PyJWTError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_runtime_token") from error
    try:
        duplicate, publish = apply_runtime_event(db, invocation_id, payload)
    except RunEventError as error:
        db.rollback()
        raise HTTPException(status_code=error.status_code, detail=error.code) from error
    db.commit()
    for project_id, event in publish:
        hub.publish(project_id, event)
    return {"duplicate": duplicate}
