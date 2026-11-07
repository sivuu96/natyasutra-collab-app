"""Codex adapter: drives the local `codex app-server` over stdio (JSON-RPC 2.0, newline-delimited).

Verified against codex-cli 0.167.0 with its generated schema (`codex app-server generate-json-schema`):
- handshake: `initialize` request, then `initialized` notification
- `thread/start` (or `thread/resume` with a stored thread id) returns `thread.id`
- `turn/start` with `input: [{type: "text", text}]` streams `item/agentMessage/delta` and
  `item/completed`, and ends with `turn/completed` (status completed | interrupted | failed)
- approvals arrive as server requests `item/commandExecution/requestApproval` or
  `item/fileChange/requestApproval`; the client answers `{"decision": "accept" | "decline" | "cancel"}`
- `turn/interrupt` cancels a running turn

One app-server process runs per invocation and is closed when the turn ends. Continuity across
invocations comes from the persisted thread (`ephemeral: false`), resumed by id.

Sandbox: threads start `read-only` with `on-request` approval, so writes and commands are either
refused by the sandbox or reported to the hub for a human decision. Authentication is the user's own
local Codex login; this module never reads Codex credential files.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import shutil
import subprocess
import sys
from collections.abc import AsyncIterator
from typing import Any

from collab_bridge.adapters.base import HarnessEvent, HarnessStatus, RunRequest

STREAM_LINE_LIMIT = 16 * 1024 * 1024
RPC_TIMEOUT_SECONDS = 60
APPROVAL_METHODS = {
    "item/commandExecution/requestApproval": "command",
    "item/fileChange/requestApproval": "file_change",
}


class CodexError(Exception):
    pass


def launch_args(binary: str) -> list[str]:
    """Windows cannot execute .cmd/.bat shims directly; route them through cmd.exe."""
    if sys.platform == "win32" and binary.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", binary, "app-server"]
    return [binary, "app-server"]


def _version_and_problem(binary: str) -> tuple[str | None, str]:
    """Version string on success; otherwise a short reason (the stderr tail stays local)."""
    args = (
        ["cmd", "/c", binary, "--version"]
        if sys.platform == "win32" and binary.lower().endswith((".cmd", ".bat"))
        else [binary, "--version"]
    )
    try:
        completed = subprocess.run(args, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        return None, type(error).__name__
    lines = (completed.stdout or "").strip().splitlines()
    if completed.returncode == 0 and lines:
        return lines[0], ""
    stderr_tail = (completed.stderr or "").strip().splitlines()[-1:] or ["no output"]
    return None, f"exit {completed.returncode}: {stderr_tail[0][:200]}"


def _find_binary(harness_bin: str | None) -> str | None:
    return harness_bin or shutil.which("codex") or shutil.which("codex.cmd")


class CodexAdapter:
    harness = "codex"

    def __init__(self) -> None:
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        self._turns: dict[str, tuple[str, str]] = {}  # invocation -> (thread_id, turn_id)
        self._ids: dict[str, itertools.count] = {}

    # ---- discovery ---------------------------------------------------------------------------

    def discover(self, harness_bin: str | None = None) -> HarnessStatus:
        path = _find_binary(harness_bin)
        if not path:
            return HarnessStatus(False, None, None, "Codex CLI not found on PATH. Install it or set harness_bin.")
        version, problem = _version_and_problem(path)
        if version is None:
            return HarnessStatus(False, None, path, f"Codex CLI did not report a version: {problem}")
        return HarnessStatus(True, version, path, "Codex CLI available. Login is checked when a run starts.")

    # ---- run ---------------------------------------------------------------------------------

    async def run(self, request: RunRequest) -> AsyncIterator[HarnessEvent]:
        binary = _find_binary(request.harness_bin)
        if not binary:
            yield HarnessEvent("failed", code="harness_unavailable", message="Codex CLI not found")
            return
        try:
            process = await asyncio.create_subprocess_exec(
                *launch_args(binary),
                cwd=str(request.cwd),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                limit=STREAM_LINE_LIMIT,
            )
        except OSError as error:
            yield HarnessEvent("failed", code="harness_unavailable", message=f"could not start Codex: {error.strerror}")
            return

        self._procs[request.invocation_id] = process
        self._ids[request.invocation_id] = itertools.count(1)
        backlog: list[dict[str, Any]] = []
        inbox: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        reader = asyncio.create_task(self._pump(process, inbox))
        try:
            await self._request(
                request.invocation_id,
                process,
                "initialize",
                {"clientInfo": {"name": "collab-bridge", "title": None, "version": "0.1.0"}},
                inbox,
                backlog,
            )
            self._notify(process, "initialized", {})

            cwd = str(request.cwd)
            policy = {"cwd": cwd, "sandbox": "read-only", "approvalPolicy": "on-request"}
            if request.resume_session:
                opened = await self._request(
                    request.invocation_id,
                    process,
                    "thread/resume",
                    {"threadId": request.resume_session, **policy},
                    inbox,
                    backlog,
                )
            else:
                opened = await self._request(
                    request.invocation_id, process, "thread/start", {**policy, "ephemeral": False}, inbox, backlog
                )
            thread_id = str(opened["thread"]["id"])
            yield HarnessEvent("session", session_ref=thread_id)

            started = await self._request(
                request.invocation_id,
                process,
                "turn/start",
                {"threadId": thread_id, "input": [{"type": "text", "text": request.prompt}]},
                inbox,
                backlog,
            )
            turn_id = str(started["turn"]["id"])
            self._turns[request.invocation_id] = (thread_id, turn_id)

            while True:
                message = backlog.pop(0) if backlog else await inbox.get()
                if message is None:
                    yield HarnessEvent("failed", code="harness_exit", message="Codex exited before the turn finished.")
                    return
                event = self._translate(request.invocation_id, process, message, turn_id, thread_id)
                if event is None:
                    continue
                yield event
                if event.kind in ("completed", "failed"):
                    return
        finally:
            self._turns.pop(request.invocation_id, None)
            self._procs.pop(request.invocation_id, None)
            self._ids.pop(request.invocation_id, None)
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await reader
            if process.returncode is None:
                process.terminate()
                with contextlib.suppress(asyncio.TimeoutError, ProcessLookupError):
                    await asyncio.wait_for(process.wait(), timeout=10)
            # Give the pipe transports a few loop turns to close before the loop can shut down.
            if process.stdin is not None:
                with contextlib.suppress(Exception):
                    process.stdin.close()
            for _ in range(5):
                await asyncio.sleep(0)

    # ---- approvals and cancellation ----------------------------------------------------------

    async def answer_approval(self, invocation_id: str, approval_key: str, approved: bool) -> None:
        process = self._procs.get(invocation_id)
        if process is None or process.stdin is None:
            return
        request_id = json.loads(approval_key)  # the server's own JSON-RPC id, kept in its original type
        decision = "accept" if approved else "decline"
        self._send(process, {"id": request_id, "result": {"decision": decision}})

    async def cancel(self, invocation_id: str) -> None:
        process = self._procs.get(invocation_id)
        turn = self._turns.get(invocation_id)
        if process is None:
            return
        if turn is None:
            process.terminate()
            return
        thread_id, turn_id = turn
        request_id = next(self._ids.get(invocation_id, itertools.count(900)))
        self._send(
            process,
            {"id": request_id, "method": "turn/interrupt", "params": {"threadId": thread_id, "turnId": turn_id}},
        )

    # ---- JSON-RPC plumbing -------------------------------------------------------------------

    async def _pump(self, process: asyncio.subprocess.Process, inbox: asyncio.Queue[dict[str, Any] | None]) -> None:
        assert process.stdout is not None
        try:
            while True:
                raw = await process.stdout.readline()
                if not raw:
                    break
                with contextlib.suppress(json.JSONDecodeError):
                    message = json.loads(raw.decode("utf-8"))
                    if isinstance(message, dict):
                        inbox.put_nowait(message)
        finally:
            inbox.put_nowait(None)

    def _send(self, process: asyncio.subprocess.Process, message: dict[str, Any]) -> None:
        if process.stdin is None or process.stdin.is_closing():
            return
        process.stdin.write((json.dumps(message) + "\n").encode("utf-8"))

    def _notify(self, process: asyncio.subprocess.Process, method: str, params: dict[str, Any]) -> None:
        self._send(process, {"method": method, "params": params})

    async def _request(
        self,
        invocation_id: str,
        process: asyncio.subprocess.Process,
        method: str,
        params: dict[str, Any],
        inbox: asyncio.Queue[dict[str, Any] | None],
        backlog: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Send a request and wait for its response. Anything else that arrives is kept for the main loop."""
        request_id = next(self._ids.setdefault(invocation_id, itertools.count(1)))
        self._send(process, {"id": request_id, "method": method, "params": params})
        loop = asyncio.get_running_loop()
        deadline = loop.time() + RPC_TIMEOUT_SECONDS
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise CodexError(f"no response to {method} within {RPC_TIMEOUT_SECONDS}s")
            message = await asyncio.wait_for(inbox.get(), timeout=remaining)
            if message is None:
                raise CodexError(f"Codex exited during {method}")
            if message.get("id") == request_id and ("result" in message or "error" in message):
                if "error" in message:
                    detail = (
                        message["error"].get("message", "request failed")
                        if isinstance(message["error"], dict)
                        else "request failed"
                    )
                    raise CodexError(f"{method} failed: {detail}")
                return dict(message["result"])
            backlog.append(message)

    def _translate(
        self,
        invocation_id: str,
        process: asyncio.subprocess.Process,
        message: dict[str, Any],
        turn_id: str,
        thread_id: str,
    ) -> HarnessEvent | None:
        method = message.get("method")
        params = message.get("params") or {}

        # Server-initiated requests (they carry an id). Approvals are surfaced; the rest are refused.
        if method is not None and "id" in message:
            if method in APPROVAL_METHODS:
                detail = params.get("command") or params.get("reason") or params.get("grantRoot") or ""
                return HarnessEvent(
                    "approval_request",
                    approval_key=json.dumps(message["id"]),
                    operation=APPROVAL_METHODS[method],
                    tools=[APPROVAL_METHODS[method]],
                    message=str(detail)[:1000] if detail else None,
                )
            # Everything else (user-input prompts, tool calls, token refresh, ...) is refused, never guessed at.
            self._send(
                process, {"id": message["id"], "error": {"code": -32601, "message": "not supported by collab-bridge"}}
            )
            return None

        if method == "item/agentMessage/delta":
            if params.get("turnId") == turn_id and isinstance(params.get("delta"), str):
                return HarnessEvent("text_delta", text=params["delta"])
            return None
        if method == "item/completed":
            item = params.get("item") or {}
            if item.get("type") == "agentMessage" and item.get("text"):
                return HarnessEvent("text_final", text=str(item["text"]))
            return None
        if method == "turn/completed":
            turn = params.get("turn") or {}
            if turn.get("id") not in (None, turn_id):
                return None
            status = turn.get("status")
            if status == "completed":
                return HarnessEvent("completed", session_ref=thread_id)
            if status == "interrupted":
                return HarnessEvent("failed", code="cancelled", message="The turn was interrupted.")
            error = turn.get("error") or {}
            text = error.get("message") if isinstance(error, dict) else None
            return HarnessEvent(
                "failed", code="harness_error", message=str(text or f"turn ended with status {status}")[:500]
            )
        return None
