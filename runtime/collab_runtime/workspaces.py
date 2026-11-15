"""Per-session workspaces kept on a warm instance, so a follow-up turn can resume the CLI's own conversation.

Each session gets one folder with two parts:
- `home/`: the CLI's HOME. The CLI stores each conversation there, under the folder it ran in.
- `repo/`: the clone. It must stay at the same path between turns, because the CLI files conversations by folder.

Workspaces live only in this process. A new or restarted instance has none, and the run falls back to the
transcript. Idle workspaces are removed, and there is a cap on how many are kept. On Cloud Run the filesystem is
memory, so these count against the instance's memory limit.
"""

import os
import shutil
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

IDLE_SECONDS = 30 * 60
MAX_WORKSPACES = 5


@dataclass
class Workspace:
    session_id: str
    root: str
    cli_session_id: str
    last_invocation_id: str | None
    last_used: float

    @property
    def home(self) -> str:
        return os.path.join(self.root, "home")

    @property
    def repo(self) -> str:
        return os.path.join(self.root, "repo")


class Workspaces:
    def __init__(
        self,
        base_dir: str,
        *,
        clock: Callable[[], float] = time.monotonic,
        idle_seconds: float = IDLE_SECONDS,
        max_workspaces: int = MAX_WORKSPACES,
    ) -> None:
        self._base = base_dir
        self._clock = clock
        self._idle = idle_seconds
        self._max = max_workspaces
        self._items: dict[str, Workspace] = {}
        self._lock = threading.Lock()

    def get(self, session_id: str) -> Workspace | None:
        """The workspace for a session, if this instance still has it and it has not gone idle."""
        with self._lock:
            self._purge_idle_locked()
            item = self._items.get(_checked(session_id))
            if item is not None and not os.path.isdir(item.repo):
                self._drop_locked(item.session_id)
                return None
            return item

    def create(self, session_id: str) -> Workspace:
        """Start a fresh workspace, replacing any existing one for the session. Evicts the least recently used."""
        key = _checked(session_id)
        with self._lock:
            self._purge_idle_locked()
            self._drop_locked(key)
            while len(self._items) >= self._max:
                oldest = min(self._items.values(), key=lambda item: item.last_used)
                self._drop_locked(oldest.session_id)
            root = os.path.join(self._base, key)
            item = Workspace(
                session_id=key,
                root=root,
                cli_session_id=str(uuid.uuid4()),
                last_invocation_id=None,
                last_used=self._clock(),
            )
            os.makedirs(item.home, exist_ok=True)
            os.makedirs(item.repo, exist_ok=True)
            self._items[key] = item
            return item

    def record_success(self, session_id: str, invocation_id: str) -> None:
        """The turn finished cleanly: the CLI's conversation now includes it."""
        with self._lock:
            item = self._items.get(_checked(session_id))
            if item is not None:
                item.last_invocation_id = invocation_id
                item.last_used = self._clock()

    def discard(self, session_id: str) -> None:
        """Forget a session and delete its files. Used after a failed turn, so the next turn starts clean."""
        with self._lock:
            self._drop_locked(_checked(session_id))

    def sessions(self) -> list[str]:
        with self._lock:
            return sorted(self._items)

    def _purge_idle_locked(self) -> None:
        now = self._clock()
        for item in [item for item in self._items.values() if now - item.last_used > self._idle]:
            self._drop_locked(item.session_id)

    def _drop_locked(self, key: str) -> None:
        item = self._items.pop(key, None)
        if item is not None:
            shutil.rmtree(item.root, ignore_errors=True)


def _checked(session_id: str) -> str:
    """Session IDs become folder names, so only UUIDs are accepted. Anything else is rejected."""
    try:
        return str(uuid.UUID(session_id))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("session id must be a UUID") from error
