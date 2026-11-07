"""FastAPI dependencies: database session, authenticated user, rate limits."""

import datetime as dt
from collections.abc import Iterator

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.requests import HTTPConnection

from app.core.security import hash_token
from app.db.base import utcnow
from app.models import AuthSession, User

SESSION_COOKIE = "ns_session"
CSRF_COOKIE = "ns_csrf"
CSRF_HEADER = "X-CSRF-Token"


def get_db(request: Request) -> Iterator[Session]:
    session: Session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


def as_utc(value: dt.datetime) -> dt.datetime:
    """SQLite returns naive datetimes; treat them as UTC (all writes use UTC)."""
    return value if value.tzinfo is not None else value.replace(tzinfo=dt.UTC)


def authenticate(conn: HTTPConnection, db: Session) -> User:
    """Resolve the session cookie to a user. Works for both HTTP requests and WebSockets."""
    unauthorised = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    raw_token = conn.cookies.get(SESSION_COOKIE)
    if not raw_token:
        raise unauthorised

    row = db.scalar(select(AuthSession).where(AuthSession.token_hash == hash_token(raw_token)))
    now = utcnow()
    if (
        row is None
        or row.revoked_at is not None
        or as_utc(row.idle_expires_at) <= now
        or as_utc(row.absolute_expires_at) <= now
    ):
        raise unauthorised

    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise unauthorised

    settings = conn.app.state.settings
    row.last_seen_at = now
    row.idle_expires_at = now + dt.timedelta(minutes=settings.session_idle_minutes)
    db.commit()
    return user


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    return authenticate(request, db)


def enforce_rate_limit(request: Request, key: str, limit: int, window_seconds: int) -> None:
    limiter = request.app.state.rate_limiter
    if not limiter.hit(key, limit, window_seconds):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests, try again later",
            headers={"Retry-After": str(window_seconds)},
        )


def client_ip(request: Request) -> str:
    # Behind a trusted reverse proxy, configure uvicorn --proxy-headers so request.client is the real peer.
    return request.client.host if request.client else "unknown"
