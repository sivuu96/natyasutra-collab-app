"""Registration, login, logout and the current-user endpoint."""

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import LoginIn, RegisterIn, UserOut
from app.core.security import (
    hash_password,
    hash_token,
    new_session_token,
    verify_password,
)
from app.db.base import utcnow
from app.deps import (
    SESSION_COOKIE,
    client_ip,
    enforce_rate_limit,
    get_current_user,
    get_db,
)
from app.models import AuthSession, User
from app.models.enums import AuditOutcome
from app.services.audit import record_audit
from app.services.handles import allocate_user_handle

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_session_cookie(response: Response, request: Request, token: str) -> None:
    settings = request.app.state.settings
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=settings.session_absolute_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def _start_session(db: Session, request: Request, response: Response, user: User) -> None:
    settings = request.app.state.settings
    now = utcnow()
    token = new_session_token()
    db.add(
        AuthSession(
            user_id=user.id,
            token_hash=hash_token(token),
            idle_expires_at=now + dt.timedelta(minutes=settings.session_idle_minutes),
            absolute_expires_at=now + dt.timedelta(hours=settings.session_absolute_hours),
        )
    )
    _set_session_cookie(response, request, token)


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(
    payload: RegisterIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> User:
    settings = request.app.state.settings
    if not settings.allow_registration:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Registration is closed")
    enforce_rate_limit(request, f"register:ip:{client_ip(request)}", limit=5, window_seconds=3600)

    email = payload.email.lower()
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        # Generic message: do not confirm which emails are registered.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Registration failed")

    user = User(
        email=email,
        display_name=payload.display_name.strip(),
        password_hash=hash_password(payload.password),
        handle=allocate_user_handle(db, email),
    )
    db.add(user)
    db.flush()
    record_audit(
        db,
        action="auth.register",
        actor_user_id=user.id,
        target_type="user",
        target_id=str(user.id),
        request_id=getattr(request.state, "request_id", None),
    )
    _start_session(db, request, response, user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=UserOut)
def login(
    payload: LoginIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> User:
    email = payload.email.lower()
    enforce_rate_limit(request, f"login:ip:{client_ip(request)}", limit=30, window_seconds=900)
    enforce_rate_limit(request, f"login:email:{email}", limit=10, window_seconds=900)

    user = db.scalar(select(User).where(User.email == email))
    if user is None or not user.is_active or not verify_password(user.password_hash, payload.password):
        record_audit(
            db,
            action="auth.login",
            actor_user_id=user.id if user else None,
            outcome=AuditOutcome.DENIED,
            request_id=getattr(request.state, "request_id", None),
        )
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    _start_session(db, request, response, user)
    record_audit(db, action="auth.login", actor_user_id=user.id, request_id=getattr(request.state, "request_id", None))
    db.commit()
    db.refresh(user)
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> Response:
    raw = request.cookies.get(SESSION_COOKIE)
    if raw:
        row = db.scalar(select(AuthSession).where(AuthSession.token_hash == hash_token(raw)))
        if row is not None and row.revoked_at is None:
            row.revoked_at = utcnow()
            db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user
