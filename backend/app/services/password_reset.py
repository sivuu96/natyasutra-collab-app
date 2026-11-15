"""Password reset by emailed link.

- A request creates one link and emails it. Earlier unused links for the account stop working.
- The same answer is given whether or not the account exists, so the endpoint cannot be used to find accounts.
- Completing a reset sets the new password, ends every session of the account, and audits the change.
"""

import datetime as dt
import logging
import secrets
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.security import hash_password, hash_token
from app.db.base import utcnow
from app.models import AuthSession, User
from app.models.auth import PasswordReset
from app.models.enums import AuditOutcome
from app.services.audit import record_audit
from app.services.email import EmailError, EmailSender

log = logging.getLogger("collab.password_reset")

RESET_MINUTES = 30


def _aware(value: dt.datetime) -> dt.datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=dt.UTC)


def request_password_reset(
    db: Session,
    sender: EmailSender,
    *,
    email: str,
    public_app_url: str,
    request_id: str | None,
) -> None:
    user = db.scalar(select(User).where(User.email == email))
    if user is None or not user.is_active:
        return  # the caller gives the same answer either way

    now = utcnow()
    db.execute(
        update(PasswordReset)
        .where(PasswordReset.user_id == user.id, PasswordReset.used_at.is_(None))
        .values(used_at=now)
    )
    token = secrets.token_urlsafe(32)
    db.add(
        PasswordReset(
            user_id=user.id,
            token_hash=hash_token(token),
            expires_at=now + dt.timedelta(minutes=RESET_MINUTES),
        )
    )
    record_audit(db, action="auth.password_reset_requested", actor_user_id=user.id, request_id=request_id)
    db.commit()

    link = f"{public_app_url.rstrip('/')}/reset-password?token={token}"
    try:
        sender.send(
            to=user.email,
            subject="Reset your NatyaSutra Hub password",
            text=(
                f"Hello {user.display_name},\n\n"
                f"Use this link to choose a new password. It works once and expires in {RESET_MINUTES} minutes:\n\n"
                f"{link}\n\n"
                "If you did not ask for this, you can ignore this email. Your password stays the same."
            ),
        )
    except EmailError as error:
        # The account owner never learns the difference; the operator sees it in the logs.
        log.error("password reset email not sent: %s", error)


def complete_password_reset(db: Session, *, token: str, password: str, request_id: str | None) -> bool:
    """Use a reset link. Returns False for an unknown, used or expired link."""
    row: Any = db.scalar(select(PasswordReset).where(PasswordReset.token_hash == hash_token(token)))
    if row is None or row.used_at is not None or _aware(row.expires_at) < utcnow():
        return False
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        return False

    now = utcnow()
    # Claim the link in one conditional update, so two requests cannot both use it.
    claimed = db.execute(
        update(PasswordReset).where(PasswordReset.id == row.id, PasswordReset.used_at.is_(None)).values(used_at=now)
    )
    if claimed.rowcount != 1:
        return False

    user.password_hash = hash_password(password)
    db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    record_audit(
        db,
        action="auth.password_reset_completed",
        actor_user_id=user.id,
        outcome=AuditOutcome.SUCCESS,
        request_id=request_id,
    )
    db.commit()
    return True


__all__ = ["RESET_MINUTES", "complete_password_reset", "request_password_reset"]
