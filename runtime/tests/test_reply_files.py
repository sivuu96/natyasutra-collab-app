"""Files in a reply: extracting them, the limits, and the run that sends them to the hub."""

from pathlib import Path

import pytest
from test_runtime import API_KEY, FakeHub, _config, _identity, _write_fake_harness

from collab_runtime.harness import ClaudeRunner
from collab_runtime.main import run
from collab_runtime.reply_files import (
    INSTRUCTIONS,
    MAX_FILE_BYTES,
    MAX_FILES,
    extract_reply_files,
)

FILE_HARNESS = """
import json
text = 'Here is the plan.\\n<file name="plan.md">\\n# Plan\\nstep one\\n</file>'
print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}), flush=True)
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": text, "session_id": "s"}), flush=True)
"""

ECHO_PROMPT = """
import json, sys
prompt = sys.argv[sys.argv.index("-p") + 1]
escaped = prompt.replace("<", "&lt;")  # so the echoed example block is not read as a file
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": escaped, "session_id": "s"}), flush=True)
"""


def test_a_file_block_is_taken_out_of_the_reply_and_kept() -> None:
    reply = extract_reply_files('Here.\n<file name="plan.md">\n# Plan\nstep one\n</file>\nThat is all.')
    assert reply.files == [("plan.md", "# Plan\nstep one")]
    assert "<file" not in reply.message
    assert reply.message == "Here.\n\nThat is all."


def test_a_reply_without_blocks_is_unchanged() -> None:
    reply = extract_reply_files("Just an answer.")
    assert reply.message == "Just an answer."
    assert reply.files == []


@pytest.mark.parametrize("name", ["../escape.md", "a/b.md", ".hidden", "with space.md", "x" * 121])
def test_names_outside_the_rule_are_refused_and_noted(name: str) -> None:
    reply = extract_reply_files(f'<file name="{name}">\ntext\n</file>')
    assert reply.files == []
    assert "not saved" in reply.message


def test_only_the_first_five_files_are_kept() -> None:
    blocks = "".join(f'<file name="f{n}.txt">\nx\n</file>\n' for n in range(MAX_FILES + 2))
    reply = extract_reply_files(blocks)
    assert [name for name, _ in reply.files] == [f"f{n}.txt" for n in range(MAX_FILES)]
    assert "at most 5 files" in reply.message


def test_a_repeated_name_is_not_kept_twice() -> None:
    reply = extract_reply_files('<file name="a.txt">\none\n</file><file name="a.txt">\ntwo\n</file>')
    assert reply.files == [("a.txt", "one")]
    assert "already" in reply.message


def test_a_file_over_the_size_limit_is_refused() -> None:
    reply = extract_reply_files(f'<file name="big.txt">\n{"x" * (MAX_FILE_BYTES + 1)}\n</file>')
    assert reply.files == []
    assert "larger than 100 KB" in reply.message


def test_files_are_sent_before_the_reply_and_the_reply_no_longer_contains_them(tmp_path: Path) -> None:
    hub = FakeHub()
    binary = _write_fake_harness(tmp_path, FILE_HARNESS)
    code = run(
        _config(binary),
        hub=hub,
        read_secret=lambda _project, _name: API_KEY,
        runner=ClaudeRunner(timeout_seconds=20),
        **_identity(),
    )
    assert code == 0
    kinds = [event["type"] for event in hub.events]
    assert kinds.index("file") < kinds.index("message") < kinds.index("completed")
    file_event = next(event for event in hub.events if event["type"] == "file")
    assert file_event["name"] == "plan.md"
    assert file_event["content"] == "# Plan\nstep one"
    message = next(event for event in hub.events if event["type"] == "message")
    assert message["text"] == "Here is the plan."


def test_the_agent_is_told_how_to_return_files(tmp_path: Path) -> None:
    hub = FakeHub()
    binary = _write_fake_harness(tmp_path, ECHO_PROMPT)
    run(
        _config(binary),
        hub=hub,
        read_secret=lambda _project, _name: API_KEY,
        runner=ClaudeRunner(timeout_seconds=20),
        **_identity(),
    )
    message = next(event for event in hub.events if event["type"] == "message")
    assert message["text"].replace("&lt;", "<").startswith(INSTRUCTIONS)
    assert '<file name="notes.md">' in INSTRUCTIONS
