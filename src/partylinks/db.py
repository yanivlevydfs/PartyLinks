"""Persistence: SQLAlchemy schema, engine/session factory and repository functions."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Generator, Iterator, Sequence
from contextlib import contextmanager
from functools import lru_cache
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, ClassVar, cast

from sqlalchemy import (
    JSON,
    Boolean,
    ColumnElement,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Table,
    Text,
    TypeDecorator,
    bindparam,
    create_engine,
    delete,
    insert,
    inspect,
    select,
    text,
    update,
)
from sqlalchemy import event as sa_event
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Mapped, ORMExecuteState, Session, mapped_column, relationship, sessionmaker

from partylinks.config import project_path
from partylinks.models import ScrapedEvent

log = logging.getLogger(__name__)

DB_RETRIES = 4
WRITE_WAIT_S = 120  # how long a writer queues for the write lock before "database is locked"


def with_db_retry[T](what: str, fn: Callable[[], T]) -> T:
    """Retry a blocking DB call on lock contention (SQLite 'database is locked')."""
    for attempt in range(1, DB_RETRIES + 1):
        try:
            return fn()
        except OperationalError as exc:
            if attempt == DB_RETRIES or "locked" not in str(exc).lower() and "deadlock" not in str(exc).lower():
                raise
            log.warning("%s: database busy (attempt %d/%d), retrying", what, attempt, DB_RETRIES)
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Stores UTC, always returns timezone-aware datetimes (SQLite drops tzinfo)."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        return value.astimezone(UTC).replace(tzinfo=None) if value is not None else None

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        return value.replace(tzinfo=UTC) if value is not None else None


class Base(DeclarativeBase):
    pass


class EventRow(Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    source_event_id: Mapped[str] = mapped_column(String(96))
    slug: Mapped[str | None] = mapped_column(String(200), unique=True, index=True)
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    start_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    end_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    venue_name: Mapped[str | None] = mapped_column(Text)
    venue_slug: Mapped[str | None] = mapped_column(String(120), index=True)
    address: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(String(128), index=True)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    country: Mapped[str | None] = mapped_column(String(2), index=True)
    in_target_city: Mapped[bool | None] = mapped_column(Boolean, index=True)
    genres: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_genres: Mapped[list[str]] = mapped_column(JSON, default=list)
    event_types: Mapped[list[str]] = mapped_column(JSON, default=list)
    artists: Mapped[list[str]] = mapped_column(JSON, default=list)
    lineup: Mapped[list[str]] = mapped_column(JSON, default=list)
    local_date: Mapped[date | None] = mapped_column(Date, index=True)
    day_of_week: Mapped[str | None] = mapped_column(String(16), index=True)
    time_slot: Mapped[str | None] = mapped_column(String(32), index=True)
    area: Mapped[str | None] = mapped_column(String(32), index=True)
    is_weekend: Mapped[bool | None] = mapped_column(Boolean, index=True)
    min_age: Mapped[int | None] = mapped_column(Integer)
    organizer: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(String(8), default="ILS")
    min_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    max_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    has_free_entry: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    entry_type: Mapped[str] = mapped_column(String(16), default="unknown", index=True)
    is_sold_out: Mapped[bool] = mapped_column(Boolean, default=False)
    free_sold_out: Mapped[bool] = mapped_column(Boolean, default=False)
    has_discount: Mapped[bool] = mapped_column(Boolean, default=False)
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    # False once a complete run of the source no longer lists it (cancelled / pulled / hidden);
    # set back to True as soon as the source lists it again.
    is_listed: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    # Fingerprint of what a buyer sees (tiers, prices, statuses, free entry) and when it last changed.
    offers_hash: Mapped[str | None] = mapped_column(String(40))
    offers_changed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    # Free entry stops at this time ("free until 23:30"); None = no known end. Group-wide too.
    free_entry_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    group_free_entry_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Listings of the same real event share a group; exactly one is primary (see dedup.py).
    group_id: Mapped[str | None] = mapped_column(String(128), index=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    # Best offer across every listing of the group, so a free / cheaper listing on another
    # source is never hidden behind the primary one. Filters and price sort use these.
    group_size: Mapped[int] = mapped_column(Integer, default=1)
    group_min_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), index=True)
    group_max_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    group_has_free_entry: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    group_entry_type: Mapped[str] = mapped_column(String(16), default="unknown", index=True)
    best_listing_id: Mapped[str | None] = mapped_column(String(128))

    tickets: Mapped[list[TicketRow]] = relationship(
        back_populates="event", cascade="all, delete-orphan", order_by="TicketRow.position"
    )
    images: Mapped[list[ImageRow]] = relationship(
        back_populates="event", cascade="all, delete-orphan", order_by="ImageRow.position"
    )

    @property
    def cover_url(self) -> str | None:
        """First stored artwork, served by the API at ``/images/{id}``."""
        return next((f"/images/{i.id}" for i in self.images if i.downloaded_at), None)


class TicketRow(Base):
    __tablename__ = "ticket_tiers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    source_ticket_id: Mapped[str | None] = mapped_column(String(96))
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    original_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    service_fee: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    total_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    price_rounds: Mapped[list[float]] = mapped_column(JSON, default=list)
    currency: Mapped[str] = mapped_column(String(8))
    kind: Mapped[str] = mapped_column(String(16), index=True)
    status: Mapped[str] = mapped_column(String(16))
    admits: Mapped[int] = mapped_column(Integer, default=1)
    is_free: Mapped[bool] = mapped_column(Boolean)
    pay_at_door: Mapped[bool] = mapped_column(Boolean, default=False)
    is_sold_out: Mapped[bool] = mapped_column(Boolean)
    is_active: Mapped[bool] = mapped_column(Boolean)
    remaining: Mapped[int | None] = mapped_column(Integer)
    sale_start: Mapped[datetime | None] = mapped_column(UTCDateTime)
    sale_end: Mapped[datetime | None] = mapped_column(UTCDateTime)

    event: Mapped[EventRow] = relationship(back_populates="tickets")


class ImageRow(Base):
    """Event artwork; ``downloaded_at`` is set once the image is stored."""

    __tablename__ = "event_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    url: Mapped[str] = mapped_column(Text)
    # Set once stored: relative file path (filesystem storage) or the bytes (database storage).
    local_path: Mapped[str | None] = mapped_column(Text)
    data: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)
    content_type: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    downloaded_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    error: Mapped[str | None] = mapped_column(Text)
    error_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    event: Mapped[EventRow] = relationship(back_populates="images")


class GeoCacheRow(Base):
    """One geocoder lookup (hit or miss), so each address is only ever asked once."""

    __tablename__ = "geo_cache"

    query: Mapped[str] = mapped_column(String(300), primary_key=True)
    found: Mapped[bool] = mapped_column(Boolean)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    # Nominatim's precision ("place" = house number, "road", "amenity", "city", ...) and city name.
    kind: Mapped[str | None] = mapped_column(String(32))
    city: Mapped[str | None] = mapped_column(String(120))
    label: Mapped[str | None] = mapped_column(Text)
    looked_up_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class DealChangeRow(Base):
    """One change to a deal between two scans: price, free entry, availability, tiers, listing."""

    __tablename__ = "deal_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    # new | price_down | price_up | free_entry_started | free_entry_ended | sold_out |
    # back_on_sale | tiers_changed | delisted | relisted
    kind: Mapped[str] = mapped_column(String(32), index=True)
    old: Mapped[str | None] = mapped_column(Text)
    new: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[str | None] = mapped_column(Text)


class ClickRow(Base):
    """One click on a ticket link (GET /go/...). No IP, cookie or user agent: just what was
    clicked, where and when. Not tied to events by a foreign key, so clicks outlive retention."""

    __tablename__ = "clicks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    event_id: Mapped[str] = mapped_column(String(128), index=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(String(128))
    # Where on our page: buy | alt | bar | seo (utm.PLACEMENTS); None = unknown / old link.
    placement: Mapped[str | None] = mapped_column(String(8))


class SponsoredRow(Base):
    """A paid placement in the home page's side rails, picked in the admin. No foreign key:
    retention may delete the event, and the API then simply stops showing it."""

    __tablename__ = "sponsored"

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    side: Mapped[str] = mapped_column(String(8))  # right | left (physical, not RTL start/end)
    position: Mapped[int] = mapped_column(Integer, default=0)  # order within its side
    until: Mapped[datetime | None] = mapped_column(UTCDateTime)  # campaign end; None = while the event is on
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class SponsoredStatRow(Base):
    """One sponsored event's views and clicks on one day (site time zone), for the advertiser report.
    Keeps the title so the report outlives retention deleting the event."""

    __tablename__ = "sponsored_stats"

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    views: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)


class FlagRow(Base):
    """An on/off switch flipped in the admin at runtime; kept in the database so it survives redeploys.
    A missing row means off."""

    __tablename__ = "flags"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)  # e.g. "sponsored"
    on: Mapped[bool] = mapped_column(Boolean, default=False)


def flag_on(session: Session, key: str) -> bool:
    row = session.get(FlagRow, key)
    return bool(row and row.on)


class ScrapeRunRow(Base):
    __tablename__ = "scrape_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    trigger: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), index=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    events_found: Mapped[int] = mapped_column(Integer, default=0)
    events_saved: Mapped[int] = mapped_column(Integer, default=0)
    events_failed: Mapped[int] = mapped_column(Integer, default=0)
    # Already-known events whose tickets / prices changed in this run, and events no longer listed.
    events_changed: Mapped[int | None] = mapped_column(Integer, default=0)
    events_unlisted: Mapped[int | None] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    failures: Mapped[list[str]] = mapped_column(JSON, default=list)


def _literal_default(column: Any) -> str | None:
    """SQL literal of a column's scalar Python default (bool/int/str), else None."""
    value = getattr(column.default, "arg", None)
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return None


def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")  # concurrent readers while a source writes
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
    dbapi_connection.create_function("has_word", 2, has_word, deterministic=True)


@lru_cache(maxsize=512)
def _word_re(word: str) -> re.Pattern[str]:
    return re.compile(r"(?<!\w)[בלמהוכש]?" + re.escape(word.casefold()) + r"(?!\w)")


def has_word(text: str | None, word: str | None) -> bool:
    """``word`` as a whole word of ``text``, maybe behind a one-letter Hebrew prefix ("בראפ").
    SQLite's ``has_word()``: a short search word ("ראפ") is not found inside "תראפיה"."""
    return bool(text and word and _word_re(word).search(text.casefold()))


def _sqlite_begin(conn: Any) -> None:
    """Writers take the write lock at BEGIN (IMMEDIATE), where the busy timeout applies. A deferred
    transaction that reads and then writes fails at once with "database is locked" when another
    writer committed in between (SQLITE_BUSY_SNAPSHOT), whatever the timeout. Other sessions keep
    the driver's own handling: it begins right before the first INSERT/UPDATE/DELETE."""
    options = conn.get_execution_options()
    if options.get("isolation_level") == "AUTOCOMMIT":
        return
    raw = conn.connection.dbapi_connection
    if options.get("sqlite_write"):
        raw.isolation_level = None  # the driver must not begin a second transaction
        conn.exec_driver_sql("BEGIN IMMEDIATE")
    else:
        raw.isolation_level = ""  # pooled connections are shared with writers


SQLITE_INCREMENTAL_VACUUM = 2  # PRAGMA auto_vacuum value


def lineup_contains(name: str) -> ColumnElement[bool]:
    """Events whose lineup has `name` in it (case-insensitive substring).

    The lineup is stored as JSON text with non-ASCII escaped ("\\u05e9..."), so a Hebrew name only
    matches in that escaped form; both forms are tried.
    """
    column = EventRow.lineup.cast(String)
    clauses = [
        column.ilike("%" + p.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%", escape="\\")
        for p in {name, json.dumps(name)[1:-1]}
    ]
    return clauses[0] if len(clauses) == 1 else clauses[0] | clauses[1]


def upcoming_filter() -> ColumnElement[bool]:
    """Current events only: today onwards, plus last night's parties still in progress."""
    from partylinks.config import get_settings  # local: config is loaded lazily
    from partylinks.retention import current_clause  # local: retention imports db

    settings = get_settings()
    # ...and still listed by its source (see EventRow.is_listed).
    return EventRow.is_listed.is_not(False) & current_clause(settings.retention, settings.timezone)


def offers_hash(event: ScrapedEvent) -> str:
    """Fingerprint of everything a buyer cares about. Ignores "remaining" counters, which tick constantly."""
    tiers = [
        (t.name, str(t.price), str(t.service_fee), t.price_rounds, t.status.value, t.is_free, t.pay_at_door, t.admits)
        for t in event.tickets
    ]
    data = [tiers, str(event.min_price), str(event.max_price), event.is_sold_out, event.entry_type.value]
    return hashlib.sha1(json.dumps(data, ensure_ascii=False, default=str).encode()).hexdigest()


def _note_write(state: ORMExecuteState) -> None:
    if not state.is_select:  # INSERT / UPDATE / DELETE (bulk or Core) or raw SQL
        state.session.info["wrote"] = True


def _note_flush(session: Session, _context: Any) -> None:
    session.info["wrote"] = True  # changed ORM objects


def _count_commit(session: Session) -> None:
    # After the real COMMIT: a reader that sees the new count also sees the new rows.
    if session.info.pop("wrote", False):
        Database.changes += 1


class Database:
    # Committed writing transactions in this process (any Database): cached API responses
    # keyed by it go stale the moment data changes.
    changes: ClassVar[int] = 0

    def __init__(self, url: str, echo: bool = False) -> None:
        parsed = make_url(url)
        if parsed.get_backend_name() == "sqlite" and parsed.database not in (None, "", ":memory:"):
            database = project_path(parsed.database)
            database.parent.mkdir(parents=True, exist_ok=True)
            url = parsed.set(database=str(database)).render_as_string(hide_password=False)
            parsed = make_url(url)
        is_sqlite = parsed.get_backend_name() == "sqlite"
        self.engine: Engine = create_engine(
            url,
            echo=echo,
            pool_pre_ping=not is_sqlite,
            # Sources write from separate threads: wait for locks instead of failing.
            connect_args={"timeout": WRITE_WAIT_S, "check_same_thread": False} if is_sqlite else {},
            # SQLite connections are cheap and WAL lets readers run beside writers: with the default cap
            # (5 + 10) a scrape's source threads held every connection and page requests timed out.
            **({"max_overflow": -1} if is_sqlite else {}),
        )
        if is_sqlite:
            sa_event.listen(self.engine, "connect", _sqlite_pragmas)
            sa_event.listen(self.engine, "begin", _sqlite_begin)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)
        self._writes = sessionmaker(self.engine.execution_options(sqlite_write=True), expire_on_commit=False)
        for maker in (self._sessions, self._writes):
            sa_event.listen(maker, "do_orm_execute", _note_write)
            sa_event.listen(maker, "after_flush", _note_flush)
            sa_event.listen(maker, "after_commit", _count_commit)
        # SQLite has one writer at a time: this process's writers queue here, in order, instead of
        # polling the file lock. Other processes (CLI, scripts) still wait on the busy timeout.
        self._writer = threading.Lock()
        self._writing = threading.local()

    def create_schema(self) -> None:
        """Create missing tables and add columns introduced since the DB was created.

        ponytail: additive-only auto-migration (new nullable columns); switch to Alembic
        once a change needs renames, type changes or data backfills.
        """
        Base.metadata.create_all(self.engine)
        existing = inspect(self.engine)
        with self.engine.begin() as conn:
            for table in Base.metadata.sorted_tables:
                present = {c["name"] for c in existing.get_columns(table.name)}
                for column in table.columns:
                    if column.name in present:
                        continue
                    ddl = column.type.compile(dialect=self.engine.dialect)
                    default = _literal_default(column)
                    suffix = f" DEFAULT {default}" if default is not None else ""
                    conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN "{column.name}" {ddl}{suffix}'))
                    if default is not None:  # existing rows get the model default, not NULL
                        conn.execute(text(f'UPDATE {table.name} SET "{column.name}" = {default}'))
                    log.info("schema: added column %s.%s", table.name, column.name)
                for index in table.indexes:  # indexes of columns added above
                    index.create(conn, checkfirst=True)
        if self.is_sqlite:
            self._enable_incremental_vacuum()

    # ------------------------------------------------------------------ SQLite disk space
    @property
    def is_sqlite(self) -> bool:
        return self.engine.dialect.name == "sqlite"

    def _autocommit(self) -> Any:
        return self.engine.connect().execution_options(isolation_level="AUTOCOMMIT")

    def _enable_incremental_vacuum(self) -> None:
        """One-time switch to ``auto_vacuum=INCREMENTAL`` so purges can shrink the file cheaply."""
        with self._autocommit() as conn:
            if conn.exec_driver_sql("PRAGMA auto_vacuum").scalar() == SQLITE_INCREMENTAL_VACUUM:
                return
            log.info("sqlite: enabling incremental auto-vacuum (one-time full VACUUM)")
            try:
                conn.exec_driver_sql("PRAGMA auto_vacuum=INCREMENTAL")
                conn.exec_driver_sql("VACUUM")  # the mode only takes effect after a rebuild
            except OperationalError as exc:  # e.g. another process holds the DB; retried next start
                log.warning("sqlite: could not enable incremental auto-vacuum: %s", exc)

    def storage_stats(self) -> dict[str, int | None]:
        """DB file size and reusable (free) space, in bytes. SQLite only; None elsewhere."""
        if not self.is_sqlite:
            return {"file_bytes": None, "wal_bytes": None, "free_bytes": None}
        with self._autocommit() as conn:
            page_size = conn.exec_driver_sql("PRAGMA page_size").scalar() or 0
            pages = conn.exec_driver_sql("PRAGMA page_count").scalar() or 0
            free = conn.exec_driver_sql("PRAGMA freelist_count").scalar() or 0
        wal = Path(f"{self.engine.url.database}-wal")
        return {
            "file_bytes": page_size * pages,
            "wal_bytes": wal.stat().st_size if wal.is_file() else 0,
            "free_bytes": page_size * free,
        }

    def compact_if_bloated(self, min_free_bytes: int = 256 * 2**20, min_free_share: float = 0.25) -> int:
        """Full VACUUM when the file is mostly empty pages (after artwork moved out, or a big purge):
        it rewrites only the live data, so a few-GB file of free pages becomes a small, contiguous one.
        Returns the bytes freed; retried while a reader holds the file."""
        stats = self.storage_stats()
        file_bytes, free = stats["file_bytes"] or 0, stats["free_bytes"] or 0
        if free < min_free_bytes or free < file_bytes * min_free_share:
            return 0
        log.info("sqlite: compacting, %d of %d bytes are free pages", free, file_bytes)
        for attempt in range(5):
            try:
                return self.reclaim_space(full=True)
            except OperationalError as exc:  # "database is locked": a request was reading
                log.warning("sqlite: compaction attempt %d failed (%s), retrying", attempt + 1, exc)
                time.sleep(10)
        return 0

    def reclaim_space(self, full: bool = False) -> int:
        """Return the pages of deleted rows to the OS. Returns the bytes freed (SQLite only).

        Incremental vacuum is quick and runs alongside scrapers; ``full`` rewrites the whole
        file (also defragments) and needs the DB to be idle for a moment.
        PostgreSQL reuses deleted space through its own autovacuum, so this is a no-op there.
        """
        if not self.is_sqlite:
            return 0
        before = self.storage_stats()
        with self._autocommit() as conn:
            incremental = conn.exec_driver_sql("PRAGMA auto_vacuum").scalar() == SQLITE_INCREMENTAL_VACUUM
            if incremental and not full:
                # Each step frees one page; drain the statement on the raw cursor (SQLAlchemy would stop early).
                conn.connection.driver_connection.execute("PRAGMA incremental_vacuum").fetchall()
            else:
                conn.exec_driver_sql("VACUUM")
            conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        after = self.storage_stats()
        freed = sum(before[k] or 0 for k in ("file_bytes", "wal_bytes")) - sum(
            after[k] or 0 for k in ("file_bytes", "wal_bytes")
        )
        log.info("sqlite: reclaimed %d bytes (%s vacuum)", freed, "full" if full or not incremental else "incremental")
        return max(freed, 0)

    def mark_interrupted_runs(self) -> int:
        """Runs still 'running' when the process starts were killed mid-way: record that."""
        with self.session() as session:
            result = session.execute(
                update(ScrapeRunRow)
                .where(ScrapeRunRow.status == "running")
                .values(status="interrupted", error="process stopped before the run finished", finished_at=utcnow())
            )
            count = result.rowcount or 0
        if count:
            log.warning("marked %d unfinished runs as interrupted", count)
        return count

    @contextmanager
    def session(self, write: bool = True) -> Generator[Session]:
        """``write=False`` for page requests: they read beside a writer and never queue behind one."""
        if not (write and self.is_sqlite):
            with self._sessions() as session, session.begin():
                yield session
            return
        if getattr(self._writing, "active", False):
            raise RuntimeError("nested write session: the outer one holds the SQLite write lock")
        if not self._writer.acquire(timeout=WRITE_WAIT_S):
            raise OperationalError("BEGIN IMMEDIATE", {}, Exception("database is locked (writer queue)"))
        self._writing.active = True
        try:
            with self._writes() as session, session.begin():
                yield session
        finally:
            self._writing.active = False
            self._writer.release()


# --------------------------------------------------------------------------- writes
_EVENT_FIELDS = (
    "source", "source_event_id", "slug", "venue_slug", "title", "description", "start_at", "end_at", "venue_name", "address",
    "city", "latitude", "longitude", "country", "in_target_city", "genres", "source_genres", "event_types", "artists",
    "lineup", "local_date", "day_of_week", "time_slot", "is_weekend", "area", "min_age", "organizer", "image_url",
    "currency", "min_price", "max_price", "has_free_entry", "entry_type", "is_sold_out", "free_sold_out", "extra",
    "free_entry_until", "has_discount",
)  # fmt: skip


# Maintained by dedup.regroup; an upsert of an existing row must not reset them.
_GROUP_COLUMNS = {
    "group_id", "is_primary", "group_size", "group_min_price", "group_max_price",
    "group_has_free_entry", "group_entry_type", "best_listing_id", "group_free_entry_until",
}  # fmt: skip


def upsert_events(session: Session, events: Sequence[ScrapedEvent]) -> tuple[int, list[str]]:
    """Bulk upsert a batch of events with a fixed number of statements.

    * events  - one multi-row ``INSERT ... ON CONFLICT DO UPDATE``
    * tickets - one ``DELETE`` + one multi-row ``INSERT`` (tiers are replaced)
    * images  - one ``SELECT``, then bulk ``DELETE`` / ``UPDATE`` / ``INSERT``;
      already-downloaded artwork is kept when its URL is still listed.

    Returns the number of events written and the local files of images that were
    dropped from their event (for the caller to delete).
    """
    if not events:
        return 0, []
    now = utcnow()
    ids = [e.event_id for e in events]
    # "Changed at" only moves when the offers really changed (a new event counts as changed now).
    stored = {
        row.id: (row.offers_hash, row.offers_changed_at)
        for row in session.execute(
            select(EventRow.id, EventRow.offers_hash, EventRow.offers_changed_at).where(EventRow.id.in_(ids))
        )
    }
    hashes = {e.event_id: offers_hash(e) for e in events}

    def changed_at(event_id: str) -> datetime | None:
        if event_id not in stored:
            return now  # new event
        old_hash, old_at = stored[event_id]
        if old_hash is None:
            return old_at  # stored before fingerprints existed: no evidence of a change
        return old_at if old_hash == hashes[event_id] else now

    event_rows = [
        {"id": e.event_id, "url": str(e.url), "first_seen_at": now, "last_seen_at": now,
         "group_id": e.event_id, "is_primary": True, "group_size": 1, "group_min_price": e.min_price,
         "group_max_price": e.max_price, "group_has_free_entry": e.has_free_entry,
         "group_entry_type": e.entry_type, "best_listing_id": e.event_id, "is_listed": True,
         "group_free_entry_until": e.free_entry_until,
         "offers_hash": hashes[e.event_id], "offers_changed_at": changed_at(e.event_id),
         **{f: getattr(e, f) for f in _EVENT_FIELDS}}
        for e in sorted(events, key=lambda e: e.event_id)  # consistent lock order across writers
    ]  # fmt: skip
    changes = _deal_changes_for(session, events, hashes, now)
    session.execute(_upsert(session, EventRow, event_rows, key="id", keep=_GROUP_COLUMNS | {"id", "first_seen_at"}))
    if changes:
        session.execute(insert(DealChangeRow), changes)

    session.execute(delete(TicketRow).where(TicketRow.event_id.in_(ids)))
    ticket_rows = [
        {"event_id": e.event_id, "position": i, **tier.model_dump(mode="python")}
        for e in events
        for i, tier in enumerate(e.tickets)
    ]
    if ticket_rows:
        session.execute(insert(TicketRow), ticket_rows)

    existing = session.execute(
        select(ImageRow.id, ImageRow.event_id, ImageRow.url, ImageRow.local_path).where(ImageRow.event_id.in_(ids))
    ).all()
    known = {(row.event_id, row.url): row for row in existing}
    wanted = {(e.event_id, url): i for e in events for i, url in enumerate(e.images)}
    stale = [row for key, row in known.items() if key not in wanted]
    if stale:
        session.execute(delete(ImageRow).where(ImageRow.id.in_([row.id for row in stale])))
    moved = [{"id": known[key].id, "position": pos} for key, pos in wanted.items() if key in known]
    bulk_update_by_id(session, ImageRow, moved)
    added = [
        {"event_id": eid, "url": url, "position": pos} for (eid, url), pos in wanted.items() if (eid, url) not in known
    ]
    if added:
        session.execute(insert(ImageRow), added)
    return len(events), [row.local_path for row in stale if row.local_path]


def bulk_update_by_id(session: Session, model: type[Base], rows: list[dict[str, Any]]) -> None:
    """``UPDATE ... WHERE id = :id`` executemany via Core.

    Unlike ORM bulk update it does not fail when a row was deleted meanwhile (e.g. by
    retention); that row is simply skipped. Rows are sorted by id so concurrent writers
    lock rows in the same order (no deadlocks on PostgreSQL).
    """
    if not rows:
        return
    table = cast(Table, model.__table__)
    columns = [c for c in rows[0] if c != "id"]
    stmt = table.update().where(table.c.id == bindparam("_id")).values({c: bindparam(f"_v_{c}") for c in columns})
    params = [{"_id": r["id"], **{f"_v_{c}": r[c] for c in columns}} for r in sorted(rows, key=lambda r: r["id"])]
    session.execute(stmt, params)


def _deal_changes_for(
    session: Session, events: Sequence[ScrapedEvent], hashes: dict[str, str], now: datetime
) -> list[dict[str, Any]]:
    """Change rows for every event whose deal differs from what is stored (read before overwrite)."""
    ids = [e.event_id for e in events]
    previous = {
        r.id: r
        for r in session.execute(
            select(
                EventRow.id, EventRow.offers_hash, EventRow.min_price, EventRow.has_free_entry,
                EventRow.is_sold_out, EventRow.is_listed,
            ).where(EventRow.id.in_(ids))
        )
    }  # fmt: skip
    changed = [i for i in ids if i in previous and previous[i].offers_hash != hashes[i]]
    old_tiers: dict[str, dict[str, Any]] = defaultdict(dict)
    if changed:
        for t in session.execute(
            select(TicketRow.event_id, TicketRow.name, TicketRow.price, TicketRow.status).where(
                TicketRow.event_id.in_(changed)
            )
        ):
            old_tiers[t.event_id][t.name] = t
    rows: list[dict[str, Any]] = []
    for event in events:
        for change in deal_changes(previous.get(event.event_id), old_tiers.get(event.event_id, {}), event, hashes):
            rows.append({"event_id": event.event_id, "source": event.source, "at": now, **change})
    return rows


def deal_changes(
    previous: Any, old_tiers: dict[str, Any], event: ScrapedEvent, hashes: dict[str, str]
) -> list[dict[str, str | None]]:
    """What changed for a buyer between the stored deal and the freshly scraped one."""

    def change(kind: str, old: Any = None, new: Any = None, detail: str | None = None) -> dict[str, str | None]:
        return {"kind": kind, "old": _text(old), "new": _text(new), "detail": detail}

    if previous is None:
        return [change("new", new=event.min_price)]
    found = [change("relisted")] if previous.is_listed is False else []
    if previous.offers_hash == hashes[event.event_id]:
        return found
    if previous.min_price is not None and event.min_price is not None and previous.min_price != event.min_price:
        found.append(change("price_down" if event.min_price < previous.min_price else "price_up",
                            previous.min_price, event.min_price))  # fmt: skip
    if bool(previous.has_free_entry) != event.has_free_entry:
        found.append(change("free_entry_started" if event.has_free_entry else "free_entry_ended"))
    if bool(previous.is_sold_out) != event.is_sold_out:
        found.append(change("sold_out" if event.is_sold_out else "back_on_sale"))
    new_tiers = {t.name: t for t in event.tickets}
    detail = [f"+{name} ({_text(new_tiers[name].price)})" for name in new_tiers.keys() - old_tiers.keys()]
    detail += [f"-{name}" for name in old_tiers.keys() - new_tiers.keys()]
    for name in old_tiers.keys() & new_tiers.keys():
        before, after = old_tiers[name], new_tiers[name]
        if before.price != after.price:
            detail.append(f"~{name}: {_text(before.price)} -> {_text(after.price)}")
        if before.status != after.status.value:
            detail.append(f"~{name}: {before.status} -> {after.status.value}")
    if detail:
        found.append(change("tiers_changed", detail="; ".join(sorted(detail))[:2000]))
    return found


def record_delisted(session: Session, source: str, event_ids: Sequence[str]) -> None:
    now = utcnow()
    if event_ids:
        session.execute(
            insert(DealChangeRow),
            [{"event_id": i, "source": source, "at": now, "kind": "delisted", "old": None, "new": None,
              "detail": None} for i in event_ids],
        )  # fmt: skip


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return f"{value.normalize():f}"
    return str(value)


def _upsert(session: Session, model: type[Base], rows: list[dict[str, Any]], key: str, keep: set[str]) -> Any:
    """Dialect-specific multi-row upsert (SQLite and PostgreSQL share the syntax)."""
    dialect = session.get_bind().dialect.name
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as dialect_insert
    elif dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as dialect_insert
    else:
        raise NotImplementedError(f"bulk upsert not implemented for {dialect}")
    stmt = dialect_insert(model).values(rows)
    columns = [c for c in rows[0] if c not in keep]
    return stmt.on_conflict_do_update(index_elements=[key], set_={c: stmt.excluded[c] for c in columns})


def delete_events(session: Session, event_ids: Sequence[str]) -> list[str]:
    """Bulk delete events with their tickets and images. Returns local artwork files to remove."""
    files: list[str] = []
    for chunk in _chunks(list(event_ids), 1000):
        files += [p for p in session.scalars(select(ImageRow.local_path).where(ImageRow.event_id.in_(chunk))) if p]
        session.execute(delete(TicketRow).where(TicketRow.event_id.in_(chunk)))
        session.execute(delete(ImageRow).where(ImageRow.event_id.in_(chunk)))
        session.execute(delete(DealChangeRow).where(DealChangeRow.event_id.in_(chunk)))
        session.execute(delete(EventRow).where(EventRow.id.in_(chunk)))
    return files


def _chunks(items: list[str], size: int) -> Iterator[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def start_run(session: Session, source: str, trigger: str) -> ScrapeRunRow:
    run = ScrapeRunRow(source=source, trigger=trigger, status="running", started_at=utcnow())
    session.add(run)
    session.flush()
    return run
