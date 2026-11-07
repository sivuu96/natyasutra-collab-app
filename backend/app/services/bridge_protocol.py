"""Wire contract between the hub and local bridges (protocol version 1).

Bridge -> hub frames:   {"type": str, "event_id": uuid, "payload": {...}}
Hub -> bridge frames:   {"type": str, "event_id": uuid, "payload": {...}}

`bridge.heartbeat` is the only frame without a meaningful event_id (it is never persisted).
The bridge package (`bridge/collab_bridge/protocol.py`) mirrors these shapes; a contract test keeps
both sides in step.
"""

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

PROTOCOL_VERSION = 1
HANDLE_PATTERN = r"^[a-z0-9][a-z0-9-]{2,39}$"
MAX_PROMPT_CHARS = 10000
MAX_DELTA_CHARS = 20000
MAX_MESSAGE_CHARS = 100000


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


INBOUND_PAYLOADS: dict[str, type[BaseModel]] = {
    "bridge.hello": HelloPayload,
    "invocation.accepted": InvocationRef,
    "invocation.rejected": InvocationRejected,
    "invocation.output_delta": OutputDelta,
    "invocation.output_message": OutputMessage,
    "invocation.completed": InvocationCompleted,
    "invocation.failed": InvocationFailed,
    "invocation.cancelled": InvocationRef,
    "approval.required": ApprovalRequired,
}

HEARTBEAT = "bridge.heartbeat"
ACK = "bridge.ack"
ERROR = "bridge.error"


class FrameError(ValueError):
    """Raised for frames that break the contract. The bridge is told and the frame is dropped."""


def validate_inbound(frame: dict[str, Any]) -> tuple[str, uuid.UUID | None, BaseModel | None]:
    if not isinstance(frame, dict):
        raise FrameError("frame must be an object")
    frame_type = frame.get("type")
    if not isinstance(frame_type, str):
        raise FrameError("frame type missing")
    if frame_type == HEARTBEAT:
        return frame_type, None, None
    model = INBOUND_PAYLOADS.get(frame_type)
    if model is None:
        raise FrameError(f"unknown frame type: {frame_type}")
    try:
        event_id = uuid.UUID(str(frame.get("event_id")))
    except ValueError as error:
        raise FrameError("event_id must be a UUID") from error
    try:
        payload = model.model_validate(frame.get("payload") or {})
    except ValidationError as error:
        raise FrameError(f"invalid payload for {frame_type}: {error.error_count()} problem(s)") from error
    return frame_type, event_id, payload
