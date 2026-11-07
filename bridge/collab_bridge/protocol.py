"""Bridge side of the hub wire contract (protocol version 1).

Mirrors backend/app/services/bridge_protocol.py. backend/tests/test_bridge_contract.py imports this
module and checks that the two agree, so drift fails the build instead of failing in production.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PROTOCOL_VERSION = 1
HANDLE_PATTERN = r"^[a-z0-9][a-z0-9-]{2,39}$"
MAX_PROMPT_CHARS = 10000
MAX_DELTA_CHARS = 20000
MAX_MESSAGE_CHARS = 100000

# Frame types the bridge sends.
HELLO = "bridge.hello"
HEARTBEAT = "bridge.heartbeat"
INVOCATION_ACCEPTED = "invocation.accepted"
INVOCATION_REJECTED = "invocation.rejected"
INVOCATION_OUTPUT_DELTA = "invocation.output_delta"
INVOCATION_OUTPUT_MESSAGE = "invocation.output_message"
INVOCATION_COMPLETED = "invocation.completed"
INVOCATION_FAILED = "invocation.failed"
INVOCATION_CANCELLED = "invocation.cancelled"
APPROVAL_REQUIRED = "approval.required"

# Frame types the hub sends.
WELCOME = "bridge.welcome"
ACK = "bridge.ack"
ERROR = "bridge.error"
INVOCATION_CREATED = "invocation.created"
INVOCATION_CANCEL = "invocation.cancel"
APPROVAL_RESOLVED = "approval.resolved"

# Frames the bridge persists in its outbox until the hub acknowledges them.
OUTBOX_TYPES = frozenset(
    {
        INVOCATION_ACCEPTED,
        INVOCATION_REJECTED,
        INVOCATION_OUTPUT_DELTA,
        INVOCATION_OUTPUT_MESSAGE,
        INVOCATION_COMPLETED,
        INVOCATION_FAILED,
        INVOCATION_CANCELLED,
        APPROVAL_REQUIRED,
    }
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentDescriptor(_Strict):
    handle: str = Field(pattern=HANDLE_PATTERN)
    display_name: str = Field(min_length=1, max_length=120)
    harness: Literal["claude_code", "codex"]
    description: str | None = Field(default=None, max_length=500)
    capabilities: list[str] = Field(default_factory=list, max_length=20)


class HelloPayload(_Strict):
    bridge_version: str = Field(max_length=40)
    agents: list[AgentDescriptor] = Field(max_length=50)


class InvocationRef(_Strict):
    invocation_id: uuid.UUID


class InvocationRejected(InvocationRef):
    reason_code: str = Field(max_length=60)
    detail: str | None = Field(default=None, max_length=500)


class OutputDelta(InvocationRef):
    text: str = Field(max_length=MAX_DELTA_CHARS)


class OutputMessage(InvocationRef):
    text: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class InvocationCompleted(InvocationRef):
    harness_session_ref: str | None = Field(default=None, max_length=120)


class InvocationFailed(InvocationRef):
    code: str = Field(max_length=60)
    message: str = Field(max_length=500)


class ApprovalRequired(InvocationRef):
    operation: str = Field(max_length=80)
    tools: list[str] = Field(default_factory=list, max_length=20)
    detail: str | None = Field(default=None, max_length=1000)


def envelope(frame_type: str, payload: dict[str, Any], event_id: uuid.UUID | None = None) -> dict[str, Any]:
    return {"type": frame_type, "event_id": str(event_id or uuid.uuid4()), "payload": payload}


def hello_frame(agents: list[AgentDescriptor], bridge_version: str) -> dict[str, Any]:
    payload = HelloPayload(bridge_version=bridge_version, agents=agents)
    return envelope(HELLO, payload.model_dump(mode="json"))


def heartbeat_frame() -> dict[str, Any]:
    return {"type": HEARTBEAT}
