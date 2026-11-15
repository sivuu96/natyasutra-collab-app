"""Runs the Claude Code CLI for one prompt and turns its stream-json output into hub events.

Security notes:
- The API key is given only to the child process environment. It is never written to disk or logged.
- Output is redacted for the key value and for anything shaped like an Anthropic key before it leaves the runtime.
- The child gets a minimal environment, an empty working directory, and read-only tools.
"""

import json
import os
import re
import subprocess
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

ANTHROPIC_KEY_SHAPE = re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}")
ALLOWED_TOOLS = ("Read", "Glob", "Grep")  # read-only; nothing that edits files or runs commands
MAX_OUTPUT_BYTES = 1_000_000


def redact(text: str, api_key: str) -> str:
    if api_key:
        text = text.replace(api_key, "[redacted]")
    return ANTHROPIC_KEY_SHAPE.sub("[redacted]", text)


def build_claude_args(binary: list[str], *, model: str, prompt: str) -> list[str]:
    # Read-only tools, the prompt last, then the output format; plus an explicit model.
    return [
        *binary,
        "--allowedTools",
        *ALLOWED_TOOLS,
        "--model",
        model,
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--verbose",
    ]


def child_env(api_key: str, home: str) -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": home,
        "LANG": "C.UTF-8",
        "ANTHROPIC_API_KEY": api_key,
    }


@dataclass
class RunOutcome:
    ok: bool
    final_text: str = ""
    error_code: str | None = None
    error_message: str | None = None
    emitted_bytes: int = 0
    notes: list[str] = field(default_factory=list)


@dataclass
class ParsedLine:
    kind: str  # "text", "result", or "other"
    text: str = ""
    is_error: bool = False


def parse_stream_line(line: str) -> ParsedLine:
    """Parse one stream-json line. Unknown or malformed lines are ignored, never fatal."""
    try:
        obj = json.loads(line)
    except ValueError:
        return ParsedLine("other")
    if not isinstance(obj, dict):
        return ParsedLine("other")
    if obj.get("type") == "assistant":
        blocks = (obj.get("message") or {}).get("content") or []
        texts = [b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
        return ParsedLine("text", text="".join(texts))
    if obj.get("type") == "result":
        return ParsedLine(
            "result",
            text=str(obj.get("result") or ""),
            is_error=bool(obj.get("is_error")) or obj.get("subtype") not in (None, "success"),
        )
    return ParsedLine("other")


class ClaudeRunner:
    """Runs one harness process at a time. `terminate()` stops it from a signal handler."""

    def __init__(self, timeout_seconds: float) -> None:
        self._timeout = timeout_seconds
        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._stopped_by_timeout = False
        self._stopped_by_request = False

    def terminate(self) -> None:
        with self._lock:
            self._stopped_by_request = True
            if self._proc is not None and self._proc.poll() is None:
                self._proc.kill()

    def run(
        self,
        *,
        command: list[str],
        api_key: str,
        workdir: str,
        emit_text: Callable[[str], None],
        cwd: str | None = None,
    ) -> RunOutcome:
        env = child_env(api_key, workdir)
        # stderr goes to a file, not a pipe: an unread pipe could fill up and stall the child.
        # Closed on every path below; a with-block would not cover the reads after the process ends.
        stderr_file = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")  # noqa: SIM115
        try:
            proc = subprocess.Popen(
                command,
                cwd=cwd or workdir,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=stderr_file,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as error:
            stderr_file.close()
            return RunOutcome(ok=False, error_code="harness_missing", error_message=type(error).__name__)

        with self._lock:
            self._proc = proc
            if self._stopped_by_request:
                proc.kill()
        timer = threading.Timer(self._timeout, self._on_timeout)
        timer.start()

        outcome = RunOutcome(ok=False)
        final: ParsedLine | None = None
        try:
            assert proc.stdout is not None
            for raw in proc.stdout:
                parsed = parse_stream_line(raw.rstrip("\n"))
                if parsed.kind == "text" and parsed.text:
                    text = redact(parsed.text, api_key)
                    if outcome.emitted_bytes + len(text) > MAX_OUTPUT_BYTES:
                        if "output_truncated" not in outcome.notes:
                            outcome.notes.append("output_truncated")
                            emit_text("\n[output truncated: limit reached]")
                        continue
                    outcome.emitted_bytes += len(text)
                    emit_text(text)
                elif parsed.kind == "result":
                    final = parsed
            returncode = proc.wait()
        finally:
            timer.cancel()
            with self._lock:
                self._proc = None
                timed_out, requested = self._stopped_by_timeout, self._stopped_by_request
            if proc.poll() is None:
                proc.kill()

        stderr_file.seek(0)
        stderr_tail = redact(stderr_file.read()[-300:], api_key)
        stderr_file.close()
        if timed_out:
            return RunOutcome(ok=False, error_code="timeout", error_message="The run exceeded its time limit.")
        if requested:
            return RunOutcome(ok=False, error_code="terminated", error_message="The run was stopped.")
        if final is None:
            return RunOutcome(ok=False, error_code="no_result", error_message=stderr_tail or "no result from harness")
        if final.is_error or returncode != 0:
            return RunOutcome(ok=False, error_code="harness_error", error_message="The agent reported an error.")
        return RunOutcome(
            ok=True,
            final_text=redact(final.text, api_key),
            emitted_bytes=outcome.emitted_bytes,
            notes=outcome.notes,
        )

    def _on_timeout(self) -> None:
        with self._lock:
            self._stopped_by_timeout = True
            if self._proc is not None and self._proc.poll() is None:
                self._proc.kill()
