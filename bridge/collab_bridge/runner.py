"""Runs invocations against local harnesses, one at a time per agent.

Responsibilities: validate hub requests against local configuration and policy, stream output as
frames, pause for human approval when a harness asks for more tools, and honour cancellation.
Every decision here is local. The hub's request is treated as a request, not as an instruction to
bypass policy.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from collab_bridge import protocol as p
from collab_bridge.adapters.base import AgentAdapter, HarnessEvent, RunRequest
from collab_bridge.config import AgentConfig, BridgeConfig
from collab_bridge.policy import PolicyError, allowed_tools_for, resolve_working_directory, tools_needing_approval

logger = logging.getLogger("collab_bridge.runner")

APPROVAL_TIMEOUT_SECONDS = 30 * 60
MAX_APPROVAL_ROUNDS = 2
Emit = Callable[[str, dict[str, Any]], None]


@dataclass
class _Work:
    invocation_id: str
    agent: AgentConfig
    prompt: str
    cwd: Path
    approved_tools: list[str] = field(default_factory=list)
    resume_session: str | None = None


class InvocationRunner:
    def __init__(
        self, config: BridgeConfig, adapters: dict[str, AgentAdapter], available: set[str], emit: Emit
    ) -> None:
        self._config = config
        self._adapters = adapters
        self._available = available  # harness names discovered at startup (not re-probed per invocation)
        self._emit = emit
        self._queues: dict[str, asyncio.Queue[_Work]] = {}
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._queued_cancel: set[str] = set()
        self._running: dict[str, AgentAdapter] = {}
        self._cancel_requested: set[str] = set()
        self._approvals: dict[str, asyncio.Future[str]] = {}

    # ---- entry points called by the client ---------------------------------------------------

    def handle_created(self, payload: p.InvocationRef | dict[str, Any]) -> None:
        data = payload if isinstance(payload, dict) else payload.model_dump(mode="json")
        invocation_id = str(data["invocation_id"])
        handle = str(data.get("agent_handle", ""))
        prompt = str(data.get("prompt", ""))

        agent = self._config.agent(handle)
        if agent is None:
            self._reject(invocation_id, "unknown_agent", f"no agent named {handle} on this machine")
            return
        if len(prompt) > p.MAX_PROMPT_CHARS:
            self._reject(invocation_id, "prompt_too_long", "prompt exceeds the bridge limit")
            return
        try:
            cwd = resolve_working_directory(agent)
        except PolicyError as error:
            self._reject(invocation_id, "policy", str(error))
            return
        adapter = self._adapters.get(agent.harness)
        if adapter is None or agent.harness not in self._available:
            self._reject(invocation_id, "harness_unavailable", f"{agent.harness} is not available on this machine")
            return

        work = _Work(
            invocation_id=invocation_id,
            agent=agent,
            prompt=prompt,
            cwd=cwd,
            resume_session=data.get("harness_session_ref"),
        )
        self._queue_for(handle).put_nowait(work)

    async def cancel(self, invocation_id: str) -> None:
        self._cancel_requested.add(invocation_id)
        if invocation_id in self._running:
            await self._running[invocation_id].cancel(invocation_id)
        else:
            self._queued_cancel.add(invocation_id)
        # A run paused on an approval must not wait out the timeout after being cancelled.
        future = self._approvals.get(invocation_id)
        if future is not None and not future.done():
            future.set_result("cancelled")

    def resolve_approval(self, invocation_id: str, decision: str) -> None:
        future = self._approvals.get(invocation_id)
        if future is not None and not future.done():
            future.set_result(decision)

    async def shutdown(self) -> None:
        for task in self._workers.values():
            task.cancel()
        for task in self._workers.values():
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    # ---- internals ---------------------------------------------------------------------------

    def _queue_for(self, handle: str) -> asyncio.Queue[_Work]:
        """The only place workers are created: the queue always exists before its worker starts."""
        if handle not in self._queues:
            self._queues[handle] = asyncio.Queue()
        if handle not in self._workers or self._workers[handle].done():
            self._workers[handle] = asyncio.create_task(self._worker(handle))
        return self._queues[handle]

    def _reject(self, invocation_id: str, reason: str, detail: str) -> None:
        logger.info("invocation rejected", extra={"invocation_id": invocation_id, "reason": reason})
        self._emit(p.INVOCATION_REJECTED, {"invocation_id": invocation_id, "reason_code": reason, "detail": detail})

    async def _worker(self, handle: str) -> None:
        queue = self._queues[handle]
        while True:
            work = await queue.get()
            try:
                await self._run(work)
            except asyncio.CancelledError:
                raise
            except Exception:  # never let one invocation kill the worker
                logger.exception("invocation crashed", extra={"invocation_id": work.invocation_id})
                self._emit(
                    p.INVOCATION_FAILED,
                    {
                        "invocation_id": work.invocation_id,
                        "code": "bridge_error",
                        "message": "The bridge hit an internal error.",
                    },
                )
            finally:
                self._queued_cancel.discard(work.invocation_id)

    async def _run(self, work: _Work) -> None:
        invocation_id = work.invocation_id
        if invocation_id in self._queued_cancel:
            self._emit(p.INVOCATION_CANCELLED, {"invocation_id": invocation_id})
            return
        self._emit(p.INVOCATION_ACCEPTED, {"invocation_id": invocation_id})
        adapter = self._adapters[work.agent.harness]

        prompt = work.prompt
        rounds = 0
        while True:
            outcome = await self._run_once(adapter, work, prompt)
            if invocation_id in self._cancel_requested:
                self._emit(p.INVOCATION_CANCELLED, {"invocation_id": invocation_id})
                return
            if outcome.failed is not None:
                self._emit(
                    p.INVOCATION_FAILED,
                    {"invocation_id": invocation_id, "code": outcome.failed[0], "message": outcome.failed[1]},
                )
                return
            if not outcome.denied_tools:
                self._emit(
                    p.INVOCATION_COMPLETED, {"invocation_id": invocation_id, "harness_session_ref": outcome.session_ref}
                )
                return

            # The harness wanted tools that policy does not allow. A human decides; nothing runs meanwhile.
            work.resume_session = outcome.session_ref or work.resume_session
            requested = tools_needing_approval(outcome.denied_tools)
            if rounds >= MAX_APPROVAL_ROUNDS or not requested:
                self._emit(
                    p.INVOCATION_COMPLETED, {"invocation_id": invocation_id, "harness_session_ref": outcome.session_ref}
                )
                return
            self._emit(
                p.APPROVAL_REQUIRED,
                {
                    "invocation_id": invocation_id,
                    "operation": "tool_use",
                    "tools": requested,
                    "detail": "The agent asked to use tools that need approval before it can continue.",
                },
            )
            decision = await self._await_decision(invocation_id)
            if decision != "approved" or invocation_id in self._cancel_requested:
                note = (
                    "Cancelled while waiting for approval."
                    if decision == "cancelled"
                    else "Tool use was not approved, so the request stopped here."
                )
                self._emit(p.INVOCATION_OUTPUT_MESSAGE, {"invocation_id": invocation_id, "text": note})
                if decision == "cancelled" or invocation_id in self._cancel_requested:
                    self._emit(p.INVOCATION_CANCELLED, {"invocation_id": invocation_id})
                else:
                    self._emit(
                        p.INVOCATION_COMPLETED,
                        {"invocation_id": invocation_id, "harness_session_ref": work.resume_session},
                    )
                return
            work.approved_tools = sorted(set(work.approved_tools) | set(requested))
            prompt = (
                f"The operator approved these tools for this request: {', '.join(requested)}. "
                "Continue the request from where you stopped."
            )
            rounds += 1

    async def _live_approval(self, invocation_id: str, adapter: AgentAdapter, event: HarnessEvent) -> None:
        operation = event.operation or "tool_use"
        self._emit(
            p.APPROVAL_REQUIRED,
            {
                "invocation_id": invocation_id,
                "operation": operation,
                "tools": [operation],
                "detail": (event.message or "The agent wants to act outside its read-only sandbox.")[:1000],
            },
        )
        decision = await self._await_decision(invocation_id)
        approved = decision == "approved" and invocation_id not in self._cancel_requested
        if event.approval_key is not None:
            await adapter.answer_approval(invocation_id, event.approval_key, approved)

    async def _await_decision(self, invocation_id: str) -> str:
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._approvals[invocation_id] = future
        try:
            return await asyncio.wait_for(future, timeout=APPROVAL_TIMEOUT_SECONDS)
        except TimeoutError:
            return "timeout"
        finally:
            self._approvals.pop(invocation_id, None)

    async def _run_once(self, adapter: AgentAdapter, work: _Work, prompt: str) -> _Outcome:
        request = RunRequest(
            invocation_id=work.invocation_id,
            prompt=prompt,
            cwd=work.cwd,
            allowed_tools=allowed_tools_for(work.approved_tools),
            resume_session=work.resume_session,
            harness_bin=work.agent.harness_bin,
        )
        outcome = _Outcome()
        self._running[work.invocation_id] = adapter
        try:
            # aclosing: stopping early must still run the adapter's cleanup (e.g. terminating its process).
            async with contextlib.aclosing(adapter.run(request)) as stream:
                async for event in stream:
                    if event.kind == "approval_request":
                        # The harness is paused; it resumes only after answer_approval. The run goes on.
                        await self._live_approval(work.invocation_id, adapter, event)
                        continue
                    self._forward(work.invocation_id, event, outcome)
                    if outcome.failed is not None or event.kind == "completed":
                        break
        except NotImplementedError as error:
            outcome.failed = ("not_implemented", str(error))
        finally:
            self._running.pop(work.invocation_id, None)
        return outcome

    def _forward(self, invocation_id: str, event: HarnessEvent, outcome: _Outcome) -> None:
        if event.kind == "text_delta" and event.text:
            self._emit(p.INVOCATION_OUTPUT_DELTA, {"invocation_id": invocation_id, "text": event.text})
        elif event.kind == "text_final" and event.text:
            self._emit(p.INVOCATION_OUTPUT_MESSAGE, {"invocation_id": invocation_id, "text": event.text})
        elif event.kind == "session" and event.session_ref:
            outcome.session_ref = event.session_ref
        elif event.kind == "permission_denied":
            outcome.denied_tools.extend(event.tools)
        elif event.kind == "completed":
            outcome.session_ref = event.session_ref or outcome.session_ref
        elif event.kind == "failed":
            outcome.failed = (event.code or "harness_error", event.message or "The harness failed.")


@dataclass
class _Outcome:
    session_ref: str | None = None
    denied_tools: list[str] = field(default_factory=list)
    failed: tuple[str, str] | None = None
