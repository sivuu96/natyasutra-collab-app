"""Claude Code adapter: runs the user's locally installed and logged-in `claude` CLI in print mode.

Verified against Claude Code 2.1.x (`claude -p --output-format stream-json --verbose
--include-partial-messages`). Output shapes used here:
- {"type":"system","subtype":"init","session_id":...}
- {"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":...}}}
- {"type":"assistant","message":{"content":[{"type":"text","text":...}]}}
- {"type":"system","subtype":"permission_denied",...} (informational)
- {"type":"result","subtype":"success","is_error":false,"session_id":...,"result":...,
   "permission_denials":[{"tool_name":...,"tool_use_id":...,"tool_input":{...}}]}
Unknown event types are ignored, so new CLI events do not break the bridge.

Credentials: none are read or copied. The CLI authenticates itself with the user's own login. The
process inherits the environment as the user's shell provides it, and nothing from it is logged.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import subprocess
from collections.abc import AsyncIterator
from typing import Any

from collab_bridge.adapters.base import HarnessEvent, HarnessStatus, RunRequest

STDERR_TAIL_CHARS = 300
STREAM_LINE_LIMIT = 16 * 1024 * 1024


def _version_of(path: str) -> str | None:
    try:
        completed = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    line = (completed.stdout or "").strip().splitlines()
    return line[0] if line else None


class ClaudeCodeAdapter:
    harness = "claude_code"

    def __init__(self) -> None:
        self._processes: dict[str, asyncio.subprocess.Process] = {}

    def discover(self, harness_bin: str | None = None) -> HarnessStatus:
        path = harness_bin or shutil.which("claude") or shutil.which("claude.cmd")
        if not path:
            return HarnessStatus(False, None, None, "Claude Code CLI not found on PATH. Install it or set harness_bin.")
        version = _version_of(path)
        if version is None:
            return HarnessStatus(False, None, path, "Claude Code CLI found but did not report a version.")
        return HarnessStatus(True, version, path, "Claude Code CLI available. Login is checked when a run starts.")

    def build_args(self, request: RunRequest) -> list[str]:
        binary = request.harness_bin or shutil.which("claude") or shutil.which("claude.cmd") or "claude"
        args = [binary, "--allowedTools", *request.allowed_tools]
        if request.resume_session:
            args += ["--resume", request.resume_session]
        args += ["-p", request.prompt, "--output-format", "stream-json", "--verbose", "--include-partial-messages"]
        return args

    async def run(self, request: RunRequest) -> AsyncIterator[HarnessEvent]:
        args = self.build_args(request)
        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                cwd=str(request.cwd),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=STREAM_LINE_LIMIT,
            )
        except OSError as error:
            yield HarnessEvent(
                "failed", code="harness_unavailable", message=f"could not start Claude Code: {error.strerror}"
            )
            return

        self._processes[request.invocation_id] = process
        saw_result = False
        try:
            assert process.stdout is not None
            while True:
                raw = await process.stdout.readline()
                if not raw:
                    break
                event = _parse_line(raw)
                for item in event:
                    if item.kind == "completed" or item.kind == "failed":
                        saw_result = True
                    yield item
        finally:
            self._processes.pop(request.invocation_id, None)

        return_code = await process.wait()
        stderr_tail = ""
        if process.stderr is not None:
            stderr_tail = (await process.stderr.read()).decode("utf-8", "replace")[-STDERR_TAIL_CHARS:]
        if not saw_result:
            yield HarnessEvent(
                "failed",
                code="harness_exit",
                message=f"Claude Code exited with code {return_code} before finishing. {_scrub(stderr_tail)}".strip(),
            )

    async def answer_approval(self, invocation_id: str, approval_key: str, approved: bool) -> None:
        # Claude Code approvals are handled by rerunning with approved tools (see runner), never live.
        return None

    async def cancel(self, invocation_id: str) -> None:
        process = self._processes.get(invocation_id)
        if process is None or process.returncode is not None:
            return
        process.terminate()
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(process.wait(), timeout=10)
        if process.returncode is None:
            process.kill()


def _scrub(text: str) -> str:
    """Remove obvious secret-shaped tokens from stderr before it leaves the machine."""
    import re

    return re.sub(r"(?i)(api[_-]?key|token|secret|password)\s*[=:]\s*\S+", r"\1=[redacted]", text).strip()


def _parse_line(raw: bytes) -> list[HarnessEvent]:
    try:
        obj: Any = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return []
    if not isinstance(obj, dict):
        return []
    kind = obj.get("type")

    if kind == "system" and obj.get("subtype") == "init" and obj.get("session_id"):
        return [HarnessEvent("session", session_ref=str(obj["session_id"]))]

    if kind == "stream_event":
        inner = obj.get("event") or {}
        delta = inner.get("delta") if isinstance(inner, dict) else None
        if isinstance(delta, dict) and delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
            return [HarnessEvent("text_delta", text=delta["text"])]
        return []

    if kind == "assistant":
        message = obj.get("message") or {}
        blocks = message.get("content") if isinstance(message, dict) else None
        texts = [b.get("text", "") for b in blocks or [] if isinstance(b, dict) and b.get("type") == "text"]
        joined = "".join(texts).strip()
        return [HarnessEvent("text_final", text=joined)] if joined else []

    if kind == "result":
        if obj.get("is_error") or obj.get("subtype") not in (None, "success"):
            return [
                HarnessEvent(
                    "failed",
                    code=f"harness_{obj.get('subtype') or 'error'}",
                    message=_scrub(str(obj.get("result") or "Claude Code reported an error."))[:500],
                )
            ]
        events: list[HarnessEvent] = []
        denials = obj.get("permission_denials") or []
        tools = sorted({str(d.get("tool_name")) for d in denials if isinstance(d, dict) and d.get("tool_name")})
        if tools:
            events.append(HarnessEvent("permission_denied", tools=tools))
        events.append(HarnessEvent("completed", session_ref=str(obj["session_id"]) if obj.get("session_id") else None))
        return events

    return []
