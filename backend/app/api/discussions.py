"""Channels, messages and threads. Every query is scoped to the project in the path."""

import datetime as dt
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import ChannelIn, ChannelOut, MessageEdit, MessageIn, MessageOut
from app.db.base import utcnow
from app.deps import get_current_user, get_db
from app.models import Agent, Channel, Message, User
from app.models.enums import AuditOutcome, ProjectRole
from app.realtime.hub import hub
from app.services.agents import Outcome, apply_outcome, create_invocations_for_message, dispatch_for_mode
from app.services.audit import record_audit
from app.services.authz import ROLE_RANK, load_project_for
from app.services.events import record_event

router = APIRouter(tags=["discussions"])

MENTION_PATTERN = re.compile(r"(?<![\w@])@([a-z0-9][a-z0-9_-]{0,39})")


def _req_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _mentions(body: str) -> list[str]:
    return sorted(set(MENTION_PATTERN.findall(body.lower())))


def _message_out(db: Session, message: Message) -> MessageOut:
    author = db.get(User, message.author_user_id) if message.author_user_id else None
    agent = db.get(Agent, message.author_agent_id) if message.author_agent_id else None
    body = "" if message.deleted_at else message.body
    return MessageOut(
        id=message.id,
        project_id=message.project_id,
        channel_id=message.channel_id,
        parent_message_id=message.parent_message_id,
        author_user_id=message.author_user_id,
        author_display_name=author.display_name if author else None,
        author_agent_handle=agent.handle if agent else None,
        body=body,
        created_at=message.created_at,
        edited_at=message.edited_at,
        deleted_at=message.deleted_at,
    )


def _channel_in_project(db: Session, project_id: uuid.UUID, channel_id: uuid.UUID) -> Channel:
    channel = db.scalar(select(Channel).where(Channel.id == channel_id, Channel.project_id == project_id))
    if channel is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return channel


def _message_in_project(db: Session, project_id: uuid.UUID, message_id: uuid.UUID) -> Message:
    message = db.scalar(select(Message).where(Message.id == message_id, Message.project_id == project_id))
    if message is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return message


@router.get("/projects/{project_id}/channels", response_model=list[ChannelOut])
def list_channels(
    project_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[Channel]:
    load_project_for(db, user, project_id)
    return list(db.scalars(select(Channel).where(Channel.project_id == project_id).order_by(Channel.name)).all())


@router.post("/projects/{project_id}/channels", response_model=ChannelOut, status_code=status.HTTP_201_CREATED)
def create_channel(
    project_id: uuid.UUID,
    payload: ChannelIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Channel:
    load_project_for(db, user, project_id, ProjectRole.MEMBER)
    if db.scalar(select(Channel.id).where(Channel.project_id == project_id, Channel.name == payload.name)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Channel already exists")
    channel = Channel(project_id=project_id, name=payload.name, topic=payload.topic)
    db.add(channel)
    db.flush()
    envelope = record_event(
        db,
        project_id=project_id,
        event_type="conversation.channel.created",
        actor_user_id=user.id,
        payload={"channel_id": str(channel.id), "name": channel.name},
    )
    record_audit(
        db,
        action="channel.created",
        actor_user_id=user.id,
        project_id=project_id,
        target_type="channel",
        target_id=str(channel.id),
        request_id=_req_id(request),
    )
    db.commit()
    db.refresh(channel)
    hub.publish(project_id, envelope)
    return channel


@router.get("/projects/{project_id}/channels/{channel_id}/messages", response_model=list[MessageOut])
def list_messages(
    project_id: uuid.UUID,
    channel_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    before: dt.datetime | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[MessageOut]:
    load_project_for(db, user, project_id)
    _channel_in_project(db, project_id, channel_id)
    query = select(Message).where(Message.channel_id == channel_id, Message.project_id == project_id)
    if before is not None:
        query = query.where(Message.created_at < before)
    rows = list(db.scalars(query.order_by(Message.created_at.desc()).limit(limit)).all())
    rows.reverse()
    return [_message_out(db, m) for m in rows]


@router.post(
    "/projects/{project_id}/channels/{channel_id}/messages",
    response_model=MessageOut,
    status_code=status.HTTP_201_CREATED,
)
def post_message(
    project_id: uuid.UUID,
    channel_id: uuid.UUID,
    payload: MessageIn,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MessageOut:
    load_project_for(db, user, project_id, ProjectRole.MEMBER)
    _channel_in_project(db, project_id, channel_id)
    if payload.parent_message_id is not None:
        parent = _message_in_project(db, project_id, payload.parent_message_id)
        if parent.channel_id != channel_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Parent message is in a different channel"
            )
    message = Message(
        project_id=project_id,
        channel_id=channel_id,
        parent_message_id=payload.parent_message_id,
        author_user_id=user.id,
        body=payload.body,
    )
    db.add(message)
    db.flush()
    envelope = record_event(
        db,
        project_id=project_id,
        event_type="conversation.message.created",
        actor_user_id=user.id,
        payload={
            "message_id": str(message.id),
            "channel_id": str(channel_id),
            "parent_message_id": str(message.parent_message_id) if message.parent_message_id else None,
            "mentions": _mentions(message.body),
        },
    )
    record_audit(
        db,
        action="message.created",
        actor_user_id=user.id,
        project_id=project_id,
        target_type="message",
        target_id=str(message.id),
        request_id=_req_id(request),
    )
    # Mentions of attached agents become invocations in the same transaction as the message.
    creation = Outcome()
    invocation_ids = create_invocations_for_message(
        db,
        project_id=project_id,
        channel=db.get(Channel, channel_id),
        message=message,
        requester=user,
        queue_if_offline=payload.queue_if_offline,
        outcome=creation,
        execution_mode=request.app.state.execution_mode,
    )
    db.commit()
    db.refresh(message)
    hub.publish(project_id, envelope)
    for project_for_event, event in creation.publish:
        hub.publish(project_for_event, event)
    # Only after commit: hand work to bridges, then record delivery.
    dispatch = dispatch_for_mode(db, invocation_ids, request.app.state.execution_mode)
    db.commit()
    apply_outcome(dispatch)
    return _message_out(db, message)


@router.patch("/projects/{project_id}/messages/{message_id}", response_model=MessageOut)
def edit_message(
    project_id: uuid.UUID,
    message_id: uuid.UUID,
    payload: MessageEdit,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MessageOut:
    load_project_for(db, user, project_id, ProjectRole.MEMBER)
    message = _message_in_project(db, project_id, message_id)
    if message.author_user_id != user.id or message.deleted_at is not None:
        record_audit(
            db,
            action="message.edit_denied",
            actor_user_id=user.id,
            project_id=project_id,
            target_type="message",
            target_id=str(message_id),
            outcome=AuditOutcome.DENIED,
            request_id=_req_id(request),
        )
        db.commit()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    message.body = payload.body
    message.edited_at = utcnow()
    envelope = record_event(
        db,
        project_id=project_id,
        event_type="conversation.message.updated",
        actor_user_id=user.id,
        payload={"message_id": str(message.id), "action": "edited", "mentions": _mentions(message.body)},
    )
    record_audit(
        db,
        action="message.edited",
        actor_user_id=user.id,
        project_id=project_id,
        target_type="message",
        target_id=str(message.id),
        request_id=_req_id(request),
    )
    db.commit()
    db.refresh(message)
    hub.publish(project_id, envelope)
    return _message_out(db, message)


@router.delete("/projects/{project_id}/messages/{message_id}", response_model=MessageOut)
def delete_message(
    project_id: uuid.UUID,
    message_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MessageOut:
    _, membership = load_project_for(db, user, project_id, ProjectRole.MEMBER)
    message = _message_in_project(db, project_id, message_id)
    is_moderator = ROLE_RANK[membership.role] >= ROLE_RANK[ProjectRole.ADMIN.value]
    if message.deleted_at is not None or (message.author_user_id != user.id and not is_moderator):
        record_audit(
            db,
            action="message.delete_denied",
            actor_user_id=user.id,
            project_id=project_id,
            target_type="message",
            target_id=str(message_id),
            outcome=AuditOutcome.DENIED,
            request_id=_req_id(request),
        )
        db.commit()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    # Soft delete: keep the row so threads stay intact; the body is cleared from every response.
    message.deleted_at = utcnow()
    message.body = ""
    envelope = record_event(
        db,
        project_id=project_id,
        event_type="conversation.message.updated",
        actor_user_id=user.id,
        payload={"message_id": str(message.id), "action": "deleted"},
    )
    record_audit(
        db,
        action="message.deleted",
        actor_user_id=user.id,
        project_id=project_id,
        target_type="message",
        target_id=str(message.id),
        request_id=_req_id(request),
    )
    db.commit()
    db.refresh(message)
    hub.publish(project_id, envelope)
    return _message_out(db, message)
