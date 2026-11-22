"""Files an agent returns in its reply.

The agent cannot write files: it has read-only tools, and the CLI cannot confine writes to one folder in print mode.
So a file is a block in the reply:

    <file name="plan.md">
    ...the file's text...
    </file>

After the run, the runtime takes these blocks out of the reply, checks each one, and sends the good ones to the hub as
file events. The hub applies the same name rule and a size limit of its own.
"""

import re
from dataclasses import dataclass, field

MAX_FILES = 5
MAX_FILE_BYTES = 100_000
NAME_RULE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
BLOCK = re.compile(r'<file name="([^"]*)">\n?(.*?)\n?</file>', re.DOTALL)

# Put in front of every prompt, so the agent knows how to return a file.
INSTRUCTIONS = (
    "If the user asks for a file or a document and you cannot write it to disk, give it in your reply as one block "
    "per file, in exactly this form:\n"
    '<file name="notes.md">\n'
    "...the whole contents of the file...\n"
    "</file>\n"
    "Names may use only letters, digits, dots, dashes and underscores, for example plan.md. Give at most five files, "
    "each under 100 KB. Outside the blocks, briefly tell the user what each file contains. Do not use these blocks "
    "unless the user asked for a file.\n\n"
    "To read a PDF, use the Read tool. A PDF longer than 20 pages must be read in page ranges of at most 20 pages, "
    "for example pages 1-20, then 21-40."
)


@dataclass
class ReplyFiles:
    message: str
    files: list[tuple[str, str]] = field(default_factory=list)


def extract_reply_files(text: str) -> ReplyFiles:
    """Split a reply into the text to show and the files to keep. Refused files are noted in the text."""
    files: list[tuple[str, str]] = []
    notes: list[str] = []

    def take(match: re.Match[str]) -> str:
        name, body = match.group(1), match.group(2)
        if not NAME_RULE.match(name):
            notes.append("A file was not saved because its name is not allowed.")
        elif any(saved == name for saved, _ in files):
            notes.append(f"{name} was not saved because a file with that name is already in this reply.")
        elif len(files) >= MAX_FILES:
            notes.append(f"{name} was not saved because a reply can hold at most {MAX_FILES} files.")
        elif len(body.encode("utf-8")) > MAX_FILE_BYTES:
            notes.append(f"{name} was not saved because it is larger than 100 KB.")
        else:
            files.append((name, body))
        return ""

    message = BLOCK.sub(take, text).strip()
    if notes:
        message = "\n\n".join(part for part in (message, *notes) if part)
    return ReplyFiles(message=message, files=files)


HUB_TEXT_LIMIT = 20_000  # the hub refuses a message event longer than this with a 422
CLIPPED_NOTE = "\n\n[The reply was cut short: it is longer than the 20,000 characters the chat can hold.]"


def clip(text: str) -> str:
    """Shorten a reply to what the hub accepts, with a note, so the run still completes."""
    if len(text) <= HUB_TEXT_LIMIT:
        return text
    return text[: HUB_TEXT_LIMIT - len(CLIPPED_NOTE)] + CLIPPED_NOTE


__all__ = ["HUB_TEXT_LIMIT", "INSTRUCTIONS", "MAX_FILES", "MAX_FILE_BYTES", "ReplyFiles", "clip", "extract_reply_files"]
