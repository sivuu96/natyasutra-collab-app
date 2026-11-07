"""Operator commands for a deployed hub. Run on the server (for example in the Render shell).

    python -m scripts.manage create-user --email you@example.com --name "Your Name"
    python -m scripts.manage set-password --email you@example.com

The password is read from the terminal without echo, or from stdin with --password-stdin. It is
never accepted as a command-line argument, so it does not end up in shell history.
"""

import argparse
import getpass
import sys

from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import MIN_PASSWORD_LENGTH, hash_password
from app.db.base import utcnow
from app.db.session import make_engine, make_session_factory
from app.models import AuthSession, User
from app.services.audit import record_audit


def _password(from_stdin: bool) -> str:
    if from_stdin:
        # Windows pipes end lines with \r\n; strip both so the password is exactly what was typed.
        return sys.stdin.readline().rstrip("\r\n")
    first = getpass.getpass("New password: ")
    second = getpass.getpass("Repeat password: ")
    if first != second:
        raise SystemExit("Passwords do not match.")
    return first


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="manage")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-user", help="create an account (works while registration is closed)")
    create.add_argument("--email", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--password-stdin", action="store_true")
    reset = sub.add_parser("set-password", help="set a new password for an existing account")
    reset.add_argument("--email", required=True)
    reset.add_argument("--password-stdin", action="store_true")
    args = parser.parse_args(argv)

    password = _password(args.password_stdin)
    if len(password) < MIN_PASSWORD_LENGTH:
        print(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.", file=sys.stderr)
        return 1

    settings = get_settings()
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    email = args.email.strip().lower()
    with session_factory() as db:
        user = db.scalar(select(User).where(User.email == email))
        if args.command == "create-user":
            if user is not None:
                print("An account with that email already exists.", file=sys.stderr)
                return 1
            user = User(email=email, display_name=args.name.strip(), password_hash=hash_password(password))
            db.add(user)
            db.flush()
            record_audit(
                db, action="admin.user_created", actor_user_id=None, target_type="user", target_id=str(user.id)
            )
            print(f"Created account {email}.")
        else:
            if user is None:
                print("No account with that email.", file=sys.stderr)
                return 1
            user.password_hash = hash_password(password)
            # A reset is often a response to a compromise: end every existing session for the account.
            now = utcnow()
            for session in db.scalars(
                select(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
            ).all():
                session.revoked_at = now
            record_audit(
                db, action="admin.password_set", actor_user_id=None, target_type="user", target_id=str(user.id)
            )
            print(f"Password updated for {email}. All of its existing sessions were signed out.")
        db.commit()
    engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
