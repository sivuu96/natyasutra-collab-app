"""A session's saved state: its repository folder and Claude Code's conversation files, kept in Cloud Storage.

After a turn finishes, the runtime packs the repository folder and the CLI's session files into one archive, named by
the session ID. The next turn, on any instance, unpacks it back into place before the CLI starts with --resume.

Restoring is strict: an archive may hold only the repository folder, the CLI's project session files and its
configuration file. Links and any other path are refused, so an archive cannot write outside the run's folder.
"""

import io
import os
import re
import tarfile
from typing import Protocol

MAX_ARCHIVE_BYTES = 300 * 1024 * 1024
REPO = "repo"
HOME = "home"
CLI_PROJECTS = "home/.claude/projects"
CLI_CONFIG = "home/.claude.json"
SESSION_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class ArchiveError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class SessionStore(Protocol):
    def get(self, session_id: str) -> bytes | None: ...

    def put(self, session_id: str, data: bytes) -> None: ...


def object_name(session_id: str) -> str:
    if not SESSION_ID.match(session_id):
        raise ArchiveError("invalid_session_id")
    return f"sessions/{session_id}.tar.gz"


class GcsSessionStore:
    """Keeps one archive per session in a private bucket. Only the runtime's service account can reach it."""

    def __init__(self, bucket: str, client: object | None = None) -> None:
        if client is None:
            from google.cloud import (
                storage,  # imported here so tests need no Google client
            )

            client = storage.Client()
        self._bucket = client.bucket(bucket)  # type: ignore[attr-defined]

    def get(self, session_id: str) -> bytes | None:
        blob = self._bucket.blob(object_name(session_id))
        if not blob.exists():
            return None
        return blob.download_as_bytes()

    def put(self, session_id: str, data: bytes) -> None:
        blob = self._bucket.blob(object_name(session_id))
        blob.upload_from_string(data, content_type="application/gzip")


# Folders that are rebuilt from source and can be very large. They are left out, so the archive stays small.
EXCLUDED_DIRS = frozenset({"node_modules", ".venv", "venv", "__pycache__", ".next", ".cache", ".gradle"})


def _skip(member: tarfile.TarInfo) -> tarfile.TarInfo | None:
    """Leave out links, which could point outside the repository, and the rebuildable folders above."""
    if member.issym() or member.islnk():
        return None
    if EXCLUDED_DIRS.intersection(member.name.split("/")):
        return None
    return member


def pack(workdir: str) -> bytes:
    """Archive what a later turn needs: the repository folder (with its .git) and the CLI's session files."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        repo = os.path.join(workdir, REPO)
        if os.path.isdir(repo):
            archive.add(repo, arcname=REPO, filter=_skip)
        projects = os.path.join(workdir, CLI_PROJECTS)
        if os.path.isdir(projects):
            archive.add(projects, arcname=CLI_PROJECTS, filter=_skip)
        config = os.path.join(workdir, CLI_CONFIG)
        if os.path.isfile(config):
            archive.add(config, arcname=CLI_CONFIG, filter=_skip)
    data = buffer.getvalue()
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ArchiveError("session_too_large")
    return data


def _allowed(name: str) -> bool:
    if name in (REPO, CLI_PROJECTS, CLI_CONFIG, "home", "home/.claude"):
        return True
    return name.startswith((f"{REPO}/", f"{CLI_PROJECTS}/"))


def unpack(data: bytes, workdir: str) -> None:
    """Restore an archive into `workdir`. Anything outside the allowed paths, and any link, is refused before extraction."""
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            name = member.name
            if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                raise ArchiveError("unsafe_archive")
            if name.startswith("/") or ".." in name.split("/") or not _allowed(name):
                raise ArchiveError("unsafe_archive")
        os.makedirs(workdir, exist_ok=True)
        archive.extractall(workdir, members=members)


__all__ = [
    "MAX_ARCHIVE_BYTES",
    "ArchiveError",
    "GcsSessionStore",
    "SessionStore",
    "object_name",
    "pack",
    "unpack",
]
