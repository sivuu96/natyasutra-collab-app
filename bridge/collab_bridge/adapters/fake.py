"""Deterministic fake harness for tests and for demonstrating the hub without a model.

Behaviour is chosen by directives in the prompt:
- `[[permission:Edit]]`  -> reports a permission denial for Edit, then completes
- `[[fail]]`             -> fails with harness_error
- `[[slow]]`             -> streams slowly until cancelled
- anything else          -> echoes the prompt as streamed text
Nothing here touches the filesystem or the network.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator

from collab_bridge.adapters.base import HarnessEvent, HarnessStatus, RunRequest

_PERMISSION = re.compile(r"\[\[permission:([A-Za-z]+)\]\]")


_LIVE = re.compile(r"\[\[live-approval:([A-Za-z_]+)\]\]")


class FakeAdapter:
    harness = "fake"

    def __init__(self) -> None:
        self._cancelled: set[str] = set()
        self._answers: dict[str, asyncio.Future[bool]] = {}

    def discover(self, harness_bin: str | None = None) -> HarnessStatus:
        return HarnessStatus(True, "fake-1.0", None, "Deterministic test harness")

    async def run(self, request: RunRequest) -> AsyncIterator[HarnessEvent]:
        yield HarnessEvent("session", session_ref=f"fake-{request.invocation_id[:8]}")
        prompt = request.prompt

        if "[[fail]]" in prompt:
            yield HarnessEvent("failed", code="harness_error", message="fake harness was asked to fail")
            return

        if "[[slow]]" in prompt:
            for index in range(200):
                if request.invocation_id in self._cancelled:
                    return
                yield HarnessEvent("text_delta", text=f"tick {index} ")
                await asyncio.sleep(0.05)
            yield HarnessEvent("text_final", text="finished slowly")
            yield HarnessEvent("completed")
            return

        live = _LIVE.search(prompt)
        if live:
            key = f"live-{request.invocation_id}"
            answer: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
            self._answers[request.invocation_id] = answer
            yield HarnessEvent(
                "approval_request",
                approval_key=key,
                operation=live.group(1),
                tools=[live.group(1)],
                message=f"fake {live.group(1)} request",
            )
            try:
                approved = await answer
            finally:
                self._answers.pop(request.invocation_id, None)
            text = "applied the change" if approved else "the change was declined"
            yield HarnessEvent("text_final", text=text)
            yield HarnessEvent("completed", session_ref=f"fake-{request.invocation_id[:8]}")
            return

        denied = _PERMISSION.findall(prompt)
        if denied and request.resume_session is None:
            yield HarnessEvent("text_final", text=f"I need permission to use {', '.join(denied)}.")
            yield HarnessEvent("permission_denied", tools=sorted(set(denied)))
            yield HarnessEvent("completed", session_ref=f"fake-{request.invocation_id[:8]}")
            return

        if request.resume_session is not None:
            answer = f"Continuing with tools {', '.join(request.allowed_tools)}."
        else:
            answer = f"Echo: {_PERMISSION.sub('', prompt).strip()}"
        for word in answer.split(" "):
            if request.invocation_id in self._cancelled:
                return
            yield HarnessEvent("text_delta", text=word + " ")
        yield HarnessEvent("text_final", text=answer)
        yield HarnessEvent("completed", session_ref=f"fake-{request.invocation_id[:8]}")

    async def cancel(self, invocation_id: str) -> None:
        self._cancelled.add(invocation_id)
        pending = self._answers.get(invocation_id)
        if pending is not None and not pending.done():
            pending.set_result(False)

    async def answer_approval(self, invocation_id: str, approval_key: str, approved: bool) -> None:
        pending = self._answers.get(invocation_id)
        if pending is not None and not pending.done():
            pending.set_result(approved)
