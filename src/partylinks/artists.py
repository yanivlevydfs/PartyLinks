"""Artist dictionary in its own SQLite file (``enrichment.lineup.artists_db``).

Every lineup name ever saved is recorded here and kept after its event is purged, so a
name a source glues together ("MOSKOMILLEROADARU") can be split on artists seen before.
Rows with ``blocked = 1`` are never accepted as artists (edit the file to curate it).
"""

from __future__ import annotations

import sqlite3
import threading
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS artists (
    key        TEXT PRIMARY KEY,          -- casefolded name
    name       TEXT NOT NULL,             -- spelling as first seen
    seen       INTEGER NOT NULL DEFAULT 0,  -- times saved in a lineup
    blocked    INTEGER NOT NULL DEFAULT 0,
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL
)
"""


class ArtistBook:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()  # sources save from several threads
        with self._connect() as conn:
            conn.execute(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30)

    def load(self) -> tuple[set[str], set[str]]:
        """(known names, blocked names), both casefolded."""
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT key, blocked FROM artists").fetchall()
        return {k for k, b in rows if not b}, {k for k, b in rows if b}

    def rows(self) -> list[tuple[str, int, int]]:
        """(name, seen, blocked), most seen first."""
        with self._lock, self._connect() as conn:
            return conn.execute("SELECT name, seen, blocked FROM artists ORDER BY seen DESC, key").fetchall()

    def set(self, name: str, blocked: bool) -> None:
        """Add a name by hand, or (un)block it."""
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO artists (key, name, blocked, first_seen, last_seen) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET blocked = excluded.blocked",
                (name.strip().casefold(), name.strip(), int(blocked), now, now),
            )

    def record(self, names: Iterable[str]) -> None:
        counts = Counter(n.strip() for n in names if n.strip())
        if not counts:
            return
        now = datetime.now(UTC).isoformat(timespec="seconds")
        rows = [(n.casefold(), n, c, now, now) for n, c in counts.items()]
        with self._lock, self._connect() as conn:
            conn.executemany(
                "INSERT INTO artists (key, name, seen, first_seen, last_seen) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET seen = seen + excluded.seen, last_seen = excluded.last_seen",
                rows,
            )
