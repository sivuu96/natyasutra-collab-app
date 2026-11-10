"""Request and response schemas. Separate from ORM models so the wire format is explicit."""

import datetime as dt
import re
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.security import MIN_PASSWORD_LENGTH

SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,78}[a-z0-9]$|^[a-z0-9]$")
CHANNEL_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,78}[a-z0-9]$|^[a-z0-9]$")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---- auth -------------------------------------------------------------------------------------


class RegisterIn(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=256)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class UserOut(ORMModel):
    id: uuid.UUID
    email: str
    display_name: str


# ---- teams and projects -----------------------------------------------------------------------


class TeamIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class TeamOut(ORMModel):
    id: uuid.UUID
    name: str
    slug: str


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)


class ProjectOut(ORMModel):
    id: uuid.UUID
    team_id: uuid.UUID
    name: str
    slug: str
    description: str | None
    role: str | None = None
    latest_sequence: int | None = None


class MemberIn(BaseModel):
    email: EmailStr
    role: str = "member"


class MemberRoleIn(BaseModel):
    role: str


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    role: str
    handle: str | None = None


# ---- discussions ------------------------------------------------------------------------------


class ChannelIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    topic: str | None = Field(default=None, max_length=300)

    @field_validator("name")
    @classmethod
    def _name_format(cls, value: str) -> str:
        value = value.strip().lower()
        if not CHANNEL_NAME_PATTERN.match(value):
            raise ValueError("channel names use lowercase letters, digits and hyphens")
        return value


class ChannelOut(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    topic: str | None


class MessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=10000)
    parent_message_id: uuid.UUID | None = None


class MessageEdit(BaseModel):
    body: str = Field(min_length=1, max_length=10000)


class MessageOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    channel_id: uuid.UUID
    parent_message_id: uuid.UUID | None
    author_user_id: uuid.UUID | None
    author_display_name: str | None
    author_agent_handle: str | None = None
    body: str
    created_at: dt.datetime
    edited_at: dt.datetime | None
    deleted_at: dt.datetime | None


# ---- tasks and decisions ----------------------------------------------------------------------


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10000)
    acceptance_criteria: str | None = Field(default=None, max_length=10000)
    status: str = "backlog"
    priority: str = "medium"
    assignee_user_id: uuid.UUID | None = None
    due_date: dt.date | None = None


class TaskPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10000)
    acceptance_criteria: str | None = Field(default=None, max_length=10000)
    status: str | None = None
    priority: str | None = None
    assignee_user_id: uuid.UUID | None = None
    due_date: dt.date | None = None


class TaskOut(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    description: str | None
    acceptance_criteria: str | None
    status: str
    priority: str
    creator_user_id: uuid.UUID | None
    assignee_user_id: uuid.UUID | None
    due_date: dt.date | None
    created_at: dt.datetime
    updated_at: dt.datetime


class DecisionIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    statement: str = Field(min_length=1, max_length=10000)
    context: str | None = Field(default=None, max_length=10000)
    options: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    rationale: str | None = Field(default=None, max_length=10000)
    consequences: str | None = Field(default=None, max_length=10000)


class DecisionPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    statement: str | None = Field(default=None, min_length=1, max_length=10000)
    context: str | None = Field(default=None, max_length=10000)
    options: list[dict[str, Any]] | None = Field(default=None, max_length=20)
    rationale: str | None = Field(default=None, max_length=10000)
    consequences: str | None = Field(default=None, max_length=10000)
    status: str | None = None


class DecisionRevisionOut(ORMModel):
    revision_no: int
    snapshot: dict[str, Any]
    changed_by_user_id: uuid.UUID | None
    created_at: dt.datetime


class DecisionOut(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    statement: str
    context: str | None
    options: list[dict[str, Any]]
    rationale: str | None
    consequences: str | None
    status: str
    proposer_user_id: uuid.UUID | None
    owner_user_id: uuid.UUID | None
    current_revision: int
    created_at: dt.datetime
    updated_at: dt.datetime
