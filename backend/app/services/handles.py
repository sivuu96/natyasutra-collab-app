"""Handles for people and agents. A person's handle and an agent's handle must never be the same string,
because a mention routes to the agent when a handle matches one."""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bridge import Agent
from app.models.identity import User

HANDLE_MAX = 30
_CLEAN = re.compile(r"[^a-z0-9_-]+")


def base_handle(email: str) -> str:
    local = email.split("@", 1)[0].lower()
    cleaned = _CLEAN.sub("-", local).strip("-_")[:HANDLE_MAX].strip("-_")
    if len(cleaned) < 2 or not cleaned[0].isalnum():
        cleaned = f"user{cleaned}".strip("-_")[:HANDLE_MAX]
    return cleaned


def allocate_user_handle(db: Session, email: str, *, exclude_user_id=None) -> str:  # type: ignore[no-untyped-def]
    """A handle for a new or backfilled user, unique among people and agents."""
    base = base_handle(email)
    candidate = base
    number = 2
    while handle_taken(db, candidate, exclude_user_id=exclude_user_id):
        suffix = f"-{number}"
        candidate = base[: HANDLE_MAX - len(suffix)] + suffix
        number += 1
    return candidate


def handle_taken(db: Session, handle: str, *, exclude_user_id=None) -> bool:  # type: ignore[no-untyped-def]
    user_query = select(User.id).where(User.handle == handle)
    if exclude_user_id is not None:
        user_query = user_query.where(User.id != exclude_user_id)
    if db.scalar(user_query) is not None:
        return True
    return db.scalar(select(Agent.id).where(Agent.handle == handle)) is not None


__all__ = ["allocate_user_handle", "base_handle", "handle_taken"]
