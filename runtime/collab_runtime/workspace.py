"""A summary of a session's working folder after a turn: its files, what this turn changed, and the diff.

The summary is shown in the session screen. It is built from the repository folder only; the full files stay in the
saved state. Hashes are used to tell changes apart, so a file touched without a real change is not reported.
"""

import hashlib
import os
import subprocess
from typing import Any

from collab_runtime.session_store import EXCLUDED_DIRS

MAX_FILES = 2000  # files listed in the tree
MAX_CHANGES = 500  # changes listed for one turn
MAX_HASH_BYTES = 2 * 1024 * 1024  # larger files are compared by size and modification time
MAX_DIFF_CHARS = 100_000
GIT_TIMEOUT_SECONDS = 60

Scan = dict[str, tuple[int, str]]  # path -> (size, digest)


def _digest(path: str, size: int) -> str:
    if size > MAX_HASH_BYTES:
        return f"stat:{size}:{os.stat(path).st_mtime_ns}"
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            sha.update(block)
    return sha.hexdigest()


def scan(repo_dir: str) -> Scan:
    """Every regular file in the repository folder, except git's own folder and rebuildable folders."""
    found: Scan = {}
    for root, dirs, files in os.walk(repo_dir):
        dirs[:] = [d for d in dirs if d != ".git" and d not in EXCLUDED_DIRS]
        for name in files:
            full = os.path.join(root, name)
            if os.path.islink(full):
                continue
            rel = os.path.relpath(full, repo_dir).replace(os.sep, "/")
            size = os.path.getsize(full)
            found[rel] = (size, _digest(full, size))
            if len(found) >= MAX_FILES:
                return found
    return found


def changes(before: Scan, after: Scan) -> list[dict[str, Any]]:
    """What changed between two scans, as added, changed or removed files."""
    result: list[dict[str, Any]] = []
    for path in sorted(after):
        if path not in before:
            result.append({"path": path, "change": "added"})
        elif before[path] != after[path]:
            result.append({"path": path, "change": "changed"})
    for path in sorted(before):
        if path not in after:
            result.append({"path": path, "change": "removed"})
    return result[:MAX_CHANGES]


def _git(repo_dir: str, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-C", repo_dir, *args],
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_SECONDS,
        check=False,
        env={"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "GIT_TERMINAL_PROMPT": "0", "HOME": repo_dir},
    )


def diff(repo_dir: str) -> tuple[str | None, bool]:
    """The uncommitted changes in the folder, against the branch, including new files. Returns (text, truncated).

    Returns (None, False) when the folder is not a git repository.
    """
    if not os.path.isdir(os.path.join(repo_dir, ".git")):
        return None, False
    parts: list[str] = []
    tracked = _git(repo_dir, ["diff", "HEAD", "--no-color", "--no-ext-diff", "--no-textconv"])
    if tracked.returncode == 0:
        parts.append(tracked.stdout)
    untracked = _git(repo_dir, ["ls-files", "--others", "--exclude-standard"])
    for name in untracked.stdout.splitlines()[:MAX_CHANGES]:
        full = os.path.join(repo_dir, name)
        if not os.path.isfile(full) or os.path.getsize(full) > MAX_HASH_BYTES:
            continue
        added = _git(repo_dir, ["diff", "--no-index", "--no-color", "--no-ext-diff", "--", os.devnull, name])
        parts.append(added.stdout)
    text = "".join(parts)
    if len(text) > MAX_DIFF_CHARS:
        return text[:MAX_DIFF_CHARS], True
    return text, False


def summary(repo_dir: str, before: Scan) -> dict[str, Any]:
    """The fields the hub stores for one turn."""
    after = scan(repo_dir)
    text, truncated = diff(repo_dir)
    return {
        "file_count": len(after),
        "total_bytes": sum(size for size, _ in after.values()),
        "files": [{"path": path, "size": size} for path, (size, _) in sorted(after.items())],
        "changes": changes(before, after),
        "diff": text,
        "diff_truncated": truncated,
    }


__all__ = ["MAX_DIFF_CHARS", "Scan", "changes", "diff", "scan", "summary"]
