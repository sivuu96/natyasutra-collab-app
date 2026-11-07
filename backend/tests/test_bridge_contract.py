"""Contract test: the hub and the bridge agree on frame names, payload shapes and limits.

The bridge package lives in ../bridge. Importing it here means any drift between the two sides
fails this test instead of failing at runtime.
"""

import sys
import uuid
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.services import bridge_protocol as hub

BRIDGE_DIR = Path(__file__).resolve().parents[2] / "bridge"
if str(BRIDGE_DIR) not in sys.path:
    sys.path.insert(0, str(BRIDGE_DIR))

from collab_bridge import protocol as bridge  # noqa: E402


def test_inbound_frame_names_match() -> None:
    bridge_inbound = {
        bridge.HELLO,
        bridge.INVOCATION_ACCEPTED,
        bridge.INVOCATION_REJECTED,
        bridge.INVOCATION_OUTPUT_DELTA,
        bridge.INVOCATION_OUTPUT_MESSAGE,
        bridge.INVOCATION_COMPLETED,
        bridge.INVOCATION_FAILED,
        bridge.INVOCATION_CANCELLED,
        bridge.APPROVAL_REQUIRED,
    }
    assert set(hub.INBOUND_PAYLOADS) == bridge_inbound


def test_bridge_outbox_types_are_all_inbound_on_the_hub() -> None:
    assert set(hub.INBOUND_PAYLOADS) >= bridge.OUTBOX_TYPES


def test_hub_constants_match() -> None:
    assert hub.PROTOCOL_VERSION == bridge.PROTOCOL_VERSION
    assert hub.HANDLE_PATTERN == bridge.HANDLE_PATTERN
    assert hub.MAX_PROMPT_CHARS == bridge.MAX_PROMPT_CHARS
    assert hub.MAX_DELTA_CHARS == bridge.MAX_DELTA_CHARS
    assert hub.MAX_MESSAGE_CHARS == bridge.MAX_MESSAGE_CHARS


def test_every_frame_the_bridge_can_emit_is_accepted_by_the_hub() -> None:
    invocation = str(uuid.uuid4())
    samples = {
        bridge.HELLO: {
            "bridge_version": "x",
            "agents": [
                {
                    "handle": "claude-architecture",
                    "display_name": "C",
                    "harness": "claude_code",
                    "capabilities": ["chat"],
                }
            ],
        },
        bridge.INVOCATION_ACCEPTED: {"invocation_id": invocation},
        bridge.INVOCATION_REJECTED: {"invocation_id": invocation, "reason_code": "policy", "detail": "x"},
        bridge.INVOCATION_OUTPUT_DELTA: {"invocation_id": invocation, "text": "a"},
        bridge.INVOCATION_OUTPUT_MESSAGE: {"invocation_id": invocation, "text": "b"},
        bridge.INVOCATION_COMPLETED: {"invocation_id": invocation, "harness_session_ref": "s"},
        bridge.INVOCATION_FAILED: {"invocation_id": invocation, "code": "c", "message": "m"},
        bridge.INVOCATION_CANCELLED: {"invocation_id": invocation},
        bridge.APPROVAL_REQUIRED: {"invocation_id": invocation, "operation": "tool_use", "tools": ["Edit"]},
    }
    for frame_type, payload in samples.items():
        envelope = bridge.envelope(frame_type, payload)
        parsed_type, event_id, parsed = hub.validate_inbound(envelope)
        assert parsed_type == frame_type
        assert event_id == uuid.UUID(envelope["event_id"])
        assert parsed is not None


def test_bridge_descriptor_rejects_what_the_hub_rejects() -> None:
    bad = {"handle": "Bad Handle", "display_name": "x", "harness": "claude_code"}
    with pytest.raises(ValidationError):
        bridge.AgentDescriptor(**bad)
    with pytest.raises(ValidationError):
        hub.AgentDescriptor(**bad)


def test_unknown_frame_types_are_refused_by_the_hub() -> None:
    with pytest.raises(hub.FrameError):
        hub.validate_inbound({"type": "shell.exec", "event_id": str(uuid.uuid4()), "payload": {"cmd": "rm -rf /"}})
