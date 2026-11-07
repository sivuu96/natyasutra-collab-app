"""Common adapter interface for local harnesses.

Adapters translate a harness's native output into HarnessEvent values. They never see hub
credentials, never accept paths from the hub, and never run shell commands on the hub's behalf.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol


@dataclass(frozen=True)
class HarnessStatus:
    available: bool
    version: str | None
    path: str | None
    detail: str


@dataclass(frozen=True)
class RunRequest:
    invocation_id: str
    prompt: str
    cwd: Path
    allowed_tools: list[str]
    resume_session: str | None = None
    harness_bin: str | None = None


EventKind = Literal[
    "text_delta",  # live fragment of the answer (not persisted by itself)
    "text_final",  # a complete assistant text block (persisted as a message)
    "session",  # harness session identifier, for resume
    "permission_denied",  # harness wanted tools that policy does not allow yet (run ends; rerun on approval)
    "approval_request",  # harness is paused on a live approval; answer with answer_approval (run continues)
    "completed",
    "failed",
]


@dataclass(frozen=True)
class HarnessEvent:
    kind: EventKind
    text: str | None = None
    session_ref: str | None = None
    tools: list[str] = field(default_factory=list)
    code: str | None = None
    message: str | None = None
    # For approval_request: an opaque key the adapter understands, and what kind of action is pending.
    approval_key: str | None = None
    operation: str | None = None


class AgentAdapter(Protocol):
    harness: str

    def discover(self, harness_bin: str | None = None) -> HarnessStatus: ...

    def run(self, request: RunRequest) -> AsyncIterator[HarnessEvent]: ...

    async def cancel(self, invocation_id: str) -> None: ...

    async def answer_approval(self, invocation_id: str, approval_key: str, approved: bool) -> None: ...
