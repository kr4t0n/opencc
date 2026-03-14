from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path


class SessionStore(ABC):
    """Abstract interface for persisting session_key -> session_id mappings."""

    @abstractmethod
    def get(self, session_key: str) -> str | None:
        """Return the session_id for the given key, or None."""

    @abstractmethod
    def put(self, session_key: str, session_id: str) -> None:
        """Persist a session_key -> session_id mapping."""

    @abstractmethod
    def delete(self, session_key: str) -> None:
        """Remove a mapping."""

    @abstractmethod
    def all(self) -> dict[str, str]:
        """Return all stored mappings as {session_key: session_id}."""

    @abstractmethod
    def close(self) -> None:
        """Release any resources (e.g., close DB connection)."""


class SqliteSessionStore(SessionStore):
    """SQLite-backed session store."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS sessions (  session_key TEXT PRIMARY KEY,  session_id TEXT NOT NULL)"
        )
        self._conn.commit()

    def get(self, session_key: str) -> str | None:
        row = self._conn.execute("SELECT session_id FROM sessions WHERE session_key = ?", (session_key,)).fetchone()
        return row[0] if row else None

    def put(self, session_key: str, session_id: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO sessions (session_key, session_id) VALUES (?, ?)",
            (session_key, session_id),
        )
        self._conn.commit()

    def delete(self, session_key: str) -> None:
        self._conn.execute("DELETE FROM sessions WHERE session_key = ?", (session_key,))
        self._conn.commit()

    def all(self) -> dict[str, str]:
        rows = self._conn.execute("SELECT session_key, session_id FROM sessions").fetchall()
        return dict(rows)

    def close(self) -> None:
        self._conn.close()
