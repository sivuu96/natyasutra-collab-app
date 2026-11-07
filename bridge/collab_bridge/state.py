"""Local durable state: the outbox of unacknowledged frames and key/value metadata (SQLite).

Frames are written here before they are sent. They are deleted only after the hub acknowledges the
event_id. After a crash or network loss, everything still unacknowledged is re-sent in order, and the
hub de-duplicates by event_id.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any


class BridgeState:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS outbox (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    frame TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                """
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- outbox -----------------------------------------------------------------------------

    def enqueue(self, frame: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO outbox (event_id, frame) VALUES (?, ?)", (frame["event_id"], json.dumps(frame))
            )
            self._conn.commit()

    def pending(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT frame FROM outbox ORDER BY seq").fetchall()
        return [json.loads(row[0]) for row in rows]

    def acknowledge(self, event_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM outbox WHERE event_id = ?", (event_id,))
            self._conn.commit()

    def outbox_size(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0])

    # ---- metadata ---------------------------------------------------------------------------

    def get(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
            self._conn.commit()

    def delete(self, key: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM meta WHERE key = ?", (key,))
            self._conn.commit()
