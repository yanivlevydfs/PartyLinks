"""Artist dictionary in the main database (tables ``artists`` and ``artist_profiles``).

Every lineup name ever saved is recorded here and kept after its event is purged, so a name a
source glues together ("MOSKOMILLEROADARU") can be split on artists seen before. Blocked names are
never accepted as artists (``partylinks artists block``).

``artist_profiles`` holds artists a ticket site curates (Go-Out, Eventer and Mevalim artist catalogs,
or performers an event links): photo, type, genre and links. Their names are always known, and are
also spotted in event text. A profile sent again only fills in what is still missing or changed;
another source's photo / links are never wiped by a sparser profile.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import Boolean, Integer, Text, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from partylinks.artists import ArtistProfile
from partylinks.db import Base, Database, NullableJSON, UTCDateTime, dialect_insert, utcnow, with_db_retry

log = logging.getLogger(__name__)

_PROFILE_COLUMNS = ("name", "source", "kind", "genre", "image_url", "spotify_url", "profile_url", "links")


class ArtistRow(Base):
    __tablename__ = "artists"

    key: Mapped[str] = mapped_column(Text, primary_key=True)  # casefolded name
    name: Mapped[str] = mapped_column(Text)  # spelling as first seen
    seen: Mapped[int] = mapped_column(Integer, default=0)  # times saved in a lineup
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ArtistProfileRow(Base):
    __tablename__ = "artist_profiles"

    key: Mapped[str] = mapped_column(Text, primary_key=True)  # casefolded name
    name: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    kind: Mapped[str | None] = mapped_column(Text)  # dj / producer / singer
    genre: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)
    spotify_url: Mapped[str | None] = mapped_column(Text)
    profile_url: Mapped[str | None] = mapped_column(Text)
    links: Mapped[list[str] | None] = mapped_column(NullableJSON)  # Instagram, YouTube, SoundCloud, site...
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ArtistBook:
    def __init__(self, db: Database) -> None:
        self.db = db

    def load(self) -> tuple[set[str], set[str]]:
        """(known names, blocked names), both casefolded. Profiled artists are always known."""
        with self.db.session(write=False) as session:
            rows = session.execute(select(ArtistRow.key, ArtistRow.blocked)).all()
            profiled = set(session.scalars(select(ArtistProfileRow.key)))
        blocked = {k for k, b in rows if b}
        return ({k for k, b in rows if not b} | profiled) - blocked, blocked

    def profiles(self) -> dict[str, ArtistProfile]:
        """Curated artists by casefolded name."""
        with self.db.session(write=False) as session:
            rows = session.scalars(select(ArtistProfileRow)).all()
        return {
            r.key: ArtistProfile(**{c: getattr(r, c) for c in _PROFILE_COLUMNS} | {"links": list(r.links or [])})
            for r in rows
        }

    def save_profiles(self, profiles: Iterable[ArtistProfile]) -> int:
        now = utcnow()
        by_key = {
            p.name.strip().casefold(): {"key": p.name.strip().casefold(), **{c: getattr(p, c) for c in _PROFILE_COLUMNS},
                                        "name": p.name.strip(), "links": p.links or None, "updated_at": now}
            for p in profiles if p.name.strip()
        }  # fmt: skip
        rows = [by_key[k] for k in sorted(by_key)]  # one row per key, in key order (lock order)
        if not rows:
            return 0

        def save() -> None:
            with self.db.session() as session:
                stmt = dialect_insert(session, ArtistProfileRow).values(rows)
                table = ArtistProfileRow.__table__.c
                # Another source's photo / links survive a sparser profile of the same artist.
                keep = {"name", "source", "updated_at"}
                session.execute(stmt.on_conflict_do_update(index_elements=["key"], set_={
                    c: stmt.excluded[c] if c in keep else func.coalesce(stmt.excluded[c], table[c])
                    for c in (*_PROFILE_COLUMNS, "updated_at")
                }))  # fmt: skip

        with_db_retry("artist profiles", save)
        return len(rows)

    def rows(self) -> list[tuple[str, int, bool]]:
        """(name, seen, blocked), most seen first."""
        with self.db.session(write=False) as session:
            rows = session.execute(select(ArtistRow.key, ArtistRow.name, ArtistRow.seen, ArtistRow.blocked)).all()
        return [(r.name, r.seen, r.blocked) for r in sorted(rows, key=lambda r: (-r.seen, r.key))]

    def set(self, name: str, blocked: bool) -> None:
        """Add a name by hand, or (un)block it."""
        now = utcnow()

        def save() -> None:
            with self.db.session() as session:
                stmt = dialect_insert(session, ArtistRow).values(
                    key=name.strip().casefold(), name=name.strip(), seen=0, blocked=blocked, first_seen=now, last_seen=now
                )
                session.execute(stmt.on_conflict_do_update(index_elements=["key"], set_={"blocked": stmt.excluded.blocked}))

        with_db_retry("artist", save)

    def record(self, names: Iterable[str], session: Session | None = None) -> None:
        """Count lineup names. Inside ``session`` (the caller's write transaction) when given, so a batch
        of events and its artists commit together; else in a transaction of its own."""
        spelling: dict[str, str] = {}
        seen: Counter[str] = Counter()
        for name in (n.strip() for n in names):  # spellings of one name ("Dali", "DALI") are one row
            if name:
                spelling.setdefault(name.casefold(), name)
                seen[name.casefold()] += 1
        if not seen:
            return
        now = utcnow()
        rows = [{"key": k, "name": spelling[k], "seen": seen[k], "blocked": False, "first_seen": now, "last_seen": now}
                for k in sorted(seen)]  # key order: concurrent writers lock rows in the same order

        def write(session: Session) -> None:
            stmt = dialect_insert(session, ArtistRow).values(rows)
            session.execute(stmt.on_conflict_do_update(
                index_elements=["key"], set_={"seen": ArtistRow.seen + stmt.excluded.seen, "last_seen": stmt.excluded.last_seen}
            ))  # fmt: skip

        if session is not None:
            write(session)
            return

        def save() -> None:
            with self.db.session() as own:
                write(own)

        with_db_retry("artists", save)
