"""Artist dictionary in its own SQLite file (``enrichment.lineup.artists_db``).

Every lineup name ever saved is recorded here and kept after its event is purged, so a
name a source glues together ("MOSKOMILLEROADARU") can be split on artists seen before.
Rows with ``blocked = 1`` are never accepted as artists (``partylinks artists block``).

``profiles`` holds artists a ticket site curates (Go-Out, Eventer and Mevalim artist catalogs, or
performers an event links): photo, type, genre and links. Their names are always known, and are
also spotted in event text. A profile sent again only fills in what is still missing or changed;
another source's photo / links are never wiped by a sparser profile.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field, fields
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
);
CREATE TABLE IF NOT EXISTS profiles (
    key         TEXT PRIMARY KEY,         -- casefolded name
    name        TEXT NOT NULL,
    source      TEXT NOT NULL,
    kind        TEXT,                     -- dj / producer / singer
    genre       TEXT,
    image_url   TEXT,
    spotify_url TEXT,
    profile_url TEXT,
    links       TEXT,                     -- JSON list: Instagram, YouTube, SoundCloud, site...
    updated_at  TEXT NOT NULL
);
"""


@dataclass
class ArtistProfile:
    name: str
    source: str
    kind: str | None = None
    genre: str | None = None
    image_url: str | None = None
    spotify_url: str | None = None
    profile_url: str | None = None
    links: list[str] = field(default_factory=list)


_PROFILE_COLUMNS = [f.name for f in fields(ArtistProfile)]


class ArtistBook:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()  # sources save from several threads
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            if "links" not in {row[1] for row in conn.execute("PRAGMA table_info(profiles)")}:
                conn.execute("ALTER TABLE profiles ADD COLUMN links TEXT")  # books made before links existed

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30)

    def load(self) -> tuple[set[str], set[str]]:
        """(known names, blocked names), both casefolded. Profiled artists are always known."""
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT key, blocked FROM artists").fetchall()
            profiled = {k for (k,) in conn.execute("SELECT key FROM profiles")}
        blocked = {k for k, b in rows if b}
        return ({k for k, b in rows if not b} | profiled) - blocked, blocked

    def profiles(self) -> dict[str, ArtistProfile]:
        """Curated artists by casefolded name."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(f"SELECT key, {', '.join(_PROFILE_COLUMNS)} FROM profiles").fetchall()
        return {key: ArtistProfile(*values[:-1], json.loads(values[-1] or "[]")) for key, *values in rows}

    def save_profiles(self, profiles: Iterable[ArtistProfile]) -> int:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        rows = [
            {"key": p.name.strip().casefold(), **{c: getattr(p, c) for c in _PROFILE_COLUMNS},
             "name": p.name.strip(), "links": json.dumps(p.links) if p.links else None, "updated_at": now}
            for p in profiles if p.name.strip()
        ]  # fmt: skip
        if rows:
            cols = ["key", *_PROFILE_COLUMNS, "updated_at"]
            # Another source's photo / links survive a sparser profile of the same artist.
            updates = ", ".join(
                f"{c} = excluded.{c}" if c in ("name", "source", "updated_at") else f"{c} = COALESCE(excluded.{c}, {c})"
                for c in cols[1:]
            )
            with self._lock, self._connect() as conn:
                conn.executemany(
                    f"INSERT INTO profiles ({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)}) "
                    f"ON CONFLICT(key) DO UPDATE SET {updates}",
                    rows,
                )
        return len(rows)

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
