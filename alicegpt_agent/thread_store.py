from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path


class ThreadStore:
    """Minimal local state for the active opaque Codex thread."""
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.lock = threading.Lock()
        with self.connection:
            self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS routes (route_key TEXT PRIMARY KEY, thread_id TEXT, updated_at REAL NOT NULL);
            DROP TABLE IF EXISTS deliveries;
            """)

    def active_thread(self, route_key: str, idle_seconds: float) -> str | None:
        """Return a thread only while it has recent activity, otherwise expire it."""
        now = time.time()
        with self.lock, self.connection:
            row = self.connection.execute("SELECT thread_id, updated_at FROM routes WHERE route_key=?", (route_key,)).fetchone()
            if not row:
                return None
            if now - row[1] >= idle_seconds:
                self.connection.execute("DELETE FROM routes WHERE route_key=?", (route_key,))
                return None
            return row[0]

    def set_thread(self, route_key: str, thread_id: str) -> None:
        with self.lock, self.connection:
            self.connection.execute("INSERT OR REPLACE INTO routes VALUES (?, ?, ?)", (route_key, thread_id, time.time()))

    def touch(self, route_key: str) -> None:
        with self.lock, self.connection:
            self.connection.execute("UPDATE routes SET updated_at=? WHERE route_key=?", (time.time(), route_key))

    def forget(self, route_key: str) -> None:
        with self.lock, self.connection:
            self.connection.execute("DELETE FROM routes WHERE route_key=?", (route_key,))

    def forget_all_routes(self) -> None:
        """Ephemeral Codex threads cannot be resumed after the daemon restarts."""
        with self.lock, self.connection:
            self.connection.execute("DELETE FROM routes")

    def close(self) -> None:
        self.connection.close()
