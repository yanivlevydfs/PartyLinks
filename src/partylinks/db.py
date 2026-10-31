"""Persistence: SQLAlchemy schema, engine/session factory and repository functions."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Generator, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, ClassVar, cast

from sqlalchemy import (
    Boolean,
    ColumnElement,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Table,
    Text,
    TypeDecorator,
    UniqueConstraint,
    bindparam,
    column,
    create_engine,
    delete,
    func,
    insert,
    inspect,
    literal,
    select,
    text,
    tuple_,
    update,
    values,
)
from sqlalchemy import event as sa_event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import URL, CursorResult, Engine, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.orm import DeclarativeBase, Mapped, ORMExecuteState, Session, mapped_column, relationship, sessionmaker

from partylinks.config import DatabaseSettings, get_settings
from partylinks.models import ScrapedEvent

log = logging.getLogger(__name__)

WRITE_WAIT_S = 120  # serialize_writes: how long a writer waits for its turn before giving up (then retried)

# PostgreSQL errors that a second try can get past: serialization failure, deadlock, lock timeout,
# server shutting down / restarting, connection lost. Never a statement timeout (57014): it would
# run the same slow statement again.
RETRYABLE_SQLSTATES = frozenset({"40001", "40P01", "55P03", "57P01", "57P02", "57P03", "08000", "08003", "08006"})


def retryable(exc: BaseException) -> bool:
    """A transient DB error: lock contention, a dropped connection or a full pool (or writer queue)."""
    if isinstance(exc, PoolTimeout):
        return True
    if not isinstance(exc, DBAPIError):
        return False
    if exc.connection_invalidated or getattr(exc.orig, "sqlstate", None) in RETRYABLE_SQLSTATES:
        return True
    return "deadlock" in str(exc.orig).lower()


def with_db_retry[T](what: str, fn: Callable[[], T]) -> T:
    """Run a DB call (its own transaction), again with backoff on a transient error. Safe: the failed
    transaction was rolled back, so nothing of it was kept."""
    retries = get_settings().database.retries
    for attempt in range(1, retries + 1):
        try:
            return fn()
        except (DBAPIError, PoolTimeout) as exc:
            if attempt == retries or not retryable(exc):
                raise
            log.warning("%s: database busy (attempt %d/%d, %s), retrying", what, attempt, retries, type(exc).__name__)
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Stores UTC (``timestamp without time zone``), always returns timezone-aware datetimes."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        # A naive value is already UTC: astimezone() would read it in the server's own zone (2-3 h off
        # on an Israeli machine, right only where TZ=UTC), so the same code stored different instants.
        if value is None or value.tzinfo is None:
            return value
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        return value.replace(tzinfo=UTC) if value is not None else None


# JSON lists / dicts: binary, indexable with GIN, compared as values (not as text).
JSONType = JSONB
# A JSON column that may be empty: Python None is SQL NULL (not JSON 'null'), so COALESCE and IS NULL see it.
NullableJSON = JSONB(none_as_null=True)


class Base(DeclarativeBase):
    pass


class EventRow(Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    source_event_id: Mapped[str] = mapped_column(Text)
    slug: Mapped[str | None] = mapped_column(Text, unique=True)
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    start_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    end_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    time_known: Mapped[bool] = mapped_column(Boolean, default=True)
    time_zone: Mapped[str | None] = mapped_column(String(48))
    venue_name: Mapped[str | None] = mapped_column(Text)
    venue_slug: Mapped[str | None] = mapped_column(Text, index=True)
    address: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(Text, index=True)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    country: Mapped[str | None] = mapped_column(String(2), index=True)
    in_target_city: Mapped[bool | None] = mapped_column(Boolean)
    genres: Mapped[list[str]] = mapped_column(JSONType, default=list)
    source_genres: Mapped[list[str]] = mapped_column(JSONType, default=list)
    event_types: Mapped[list[str]] = mapped_column(JSONType, default=list)
    artists: Mapped[list[str]] = mapped_column(JSONType, default=list)
    lineup: Mapped[list[str]] = mapped_column(JSONType, default=list)
    local_date: Mapped[date | None] = mapped_column(Date, index=True)
    day_of_week: Mapped[str | None] = mapped_column(String(16))
    time_slot: Mapped[str | None] = mapped_column(String(32))
    area: Mapped[str | None] = mapped_column(String(32), index=True)
    is_weekend: Mapped[bool | None] = mapped_column(Boolean)
    min_age: Mapped[int | None] = mapped_column(Integer)
    organizer: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(String(8), default="ILS")
    min_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    max_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    has_free_entry: Mapped[bool] = mapped_column(Boolean, default=False)
    entry_type: Mapped[str] = mapped_column(String(16), default="unknown")
    is_sold_out: Mapped[bool] = mapped_column(Boolean, default=False)
    free_sold_out: Mapped[bool] = mapped_column(Boolean, default=False)
    has_discount: Mapped[bool] = mapped_column(Boolean, default=False)
    extra: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    # False once a complete run of the source no longer lists it (cancelled / pulled / hidden);
    # set back to True as soon as the source lists it again.
    is_listed: Mapped[bool] = mapped_column(Boolean, default=True)
    # Fingerprint of what a buyer sees (tiers, prices, statuses, free entry) and when it last changed.
    offers_hash: Mapped[str | None] = mapped_column(String(40))
    offers_changed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Fingerprint of every scraped value (content_hash): a rescan that finds it unchanged only marks
    # the event seen, instead of rewriting the whole row (and its TOASTed description and extra).
    content_hash: Mapped[str | None] = mapped_column(String(40))
    # Free entry stops at this time ("free until 23:30"); None = no known end. Group-wide too.
    free_entry_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    group_free_entry_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Listings of the same real event share a group; exactly one is primary (see dedup.py).
    group_id: Mapped[str | None] = mapped_column(Text, index=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True)
    # Best offer across every listing of the group, so a free / cheaper listing on another
    # source is never hidden behind the primary one. Filters and price sort use these.
    group_size: Mapped[int] = mapped_column(Integer, default=1)
    group_min_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), index=True)
    group_max_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    group_has_free_entry: Mapped[bool] = mapped_column(Boolean, default=False)
    group_entry_type: Mapped[str] = mapped_column(String(16), default="unknown", index=True)
    best_listing_id: Mapped[str | None] = mapped_column(Text)

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


# The default list (/events by date): primary cards in (local_date, start_at, id) order, undated first,
# read in order and stopped at the page's LIMIT instead of sorting every row. Its columns barely change
# on an upsert, so it keeps updates HOT.
Index(
    "ix_events_feed",
    EventRow.local_date.asc().nulls_first(),
    EventRow.start_at.asc().nulls_first(),
    EventRow.id,
    postgresql_where=EventRow.is_primary.is_(True) & EventRow.is_listed.is_not(False),
)
# The alert matcher's "already listed by this source?" check (same source and title, any date): one probe each.
Index("ix_events_source_title", EventRow.source, EventRow.title)


class TicketRow(Base):
    __tablename__ = "ticket_tiers"

    # One tier per position of an event; also the index of event_id (its first column).
    __table_args__ = (UniqueConstraint("event_id", "position", name="uq_ticket_tiers_event_position"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)
    source_ticket_id: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    original_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    service_fee: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    total_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    price_rounds: Mapped[list[float]] = mapped_column(JSONType, default=list)
    currency: Mapped[str] = mapped_column(String(8))
    kind: Mapped[str] = mapped_column(String(16))
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
    content_type: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    downloaded_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    error: Mapped[str | None] = mapped_column(Text)
    error_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    event: Mapped[EventRow] = relationship(back_populates="images")


class GeoCacheRow(Base):
    """One geocoder lookup (hit or miss), so each address is only ever asked once."""

    __tablename__ = "geo_cache"

    query: Mapped[str] = mapped_column(Text, primary_key=True)
    found: Mapped[bool] = mapped_column(Boolean)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    # Nominatim's precision ("place" = house number, "road", "amenity", "city", ...) and city name.
    kind: Mapped[str | None] = mapped_column(String(32))
    city: Mapped[str | None] = mapped_column(Text)
    label: Mapped[str | None] = mapped_column(Text)
    looked_up_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class DealChangeRow(Base):
    """One change to a deal between two scans: price, free entry, availability, tiers, listing."""

    __tablename__ = "deal_changes"
    # An event's changes, newest first (event page); also the index of the event_id foreign key.
    __table_args__ = (Index("ix_deal_changes_event_at", "event_id", "at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"))
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
    event_id: Mapped[str] = mapped_column(Text, index=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(Text)
    # Where on our page: buy | alt | bar | seo (utm.PLACEMENTS); None = unknown / old link.
    placement: Mapped[str | None] = mapped_column(String(8))


class SearchRow(Base):
    """One search on the site (GET /events?q=...&sponsored=true, first page: the results grid). No IP, cookie or user agent: the
    words (normalised), how many events they found, when."""

    __tablename__ = "searches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    q: Mapped[str] = mapped_column(String(100))
    results: Mapped[int] = mapped_column(Integer)


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
    failures: Mapped[list[str]] = mapped_column(JSONType, default=list)


def _literal_default(column: Any, dialect: Any) -> str | None:
    """SQL literal of a column's scalar Python default (bool/int/float/str), else None."""
    value = getattr(column.default, "arg", None)
    if not isinstance(value, bool | int | float | str):
        return None
    return str(literal(value, column.type).compile(dialect=dialect, compile_kwargs={"literal_binds": True}))


# A word character, spelled out so the rule never depends on the database's collation / ctype (in a
# "C" database \w is ASCII only, and Hebrew letters would count as spaces).
_WORD_CHARS = "0-9A-Za-z_\u00c0-\u024f\u0370-\u04ff\u0590-\u06ff"  # digits, Latin, Greek, Cyrillic, Hebrew, Arabic
_REGEX_META = str.maketrans({c: "\\" + c for c in "\\.^$*+?()[]{}|-"})
_HEBREW_PREFIXES = (
    "\u05d1\u05dc\u05de\u05d4\u05d5\u05db\u05e9"  # one-letter prefixes: bet, lamed, mem, he, vav, kaf, shin
)


def whole_word(column: Any, word: str) -> ColumnElement[bool]:
    """``column`` has ``word`` as a whole word, maybe behind a one-letter Hebrew prefix ("\u05d1\u05e8\u05d0\u05e4"): a
    short search word ("\u05e8\u05d0\u05e4") is not found inside "\u05ea\u05e8\u05d0\u05e4\u05d9\u05d4". A case-insensitive regex (``~*``); the
    word is data, not a pattern (every metacharacter escaped), and the pattern is a bound parameter."""
    escaped = word.casefold().translate(_REGEX_META)
    return column.regexp_match(f"(^|[^{_WORD_CHARS}])[{_HEBREW_PREFIXES}]?{escaped}($|[^{_WORD_CHARS}])", flags="i")


def lineup_contains(name: str) -> ColumnElement[bool]:
    """Events whose lineup has `name` in it (case-insensitive substring of the JSONB list's text)."""
    escaped = name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return EventRow.lineup.cast(String).ilike(f"%{escaped}%", escape="\\")


def lineup_has(name: str) -> ColumnElement[bool]:
    """Events with `name` as one whole name of their lineup, any case: an artist's page. "Adam" is not
    "Omer Adam" or "Adam Ten" (``lineup_contains`` is the search's substring match)."""
    quoted = json.dumps(name, ensure_ascii=False)  # the element as the JSONB text spells it, quotes included
    escaped = quoted.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return EventRow.lineup.cast(String).ilike(f"%{escaped}%", escape="\\")


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


def content_hash(row: dict[str, Any]) -> str:
    """Fingerprint of an event's scraped values (``_EVENT_FIELDS``, its URL and its offers)."""
    data = {k: row[k] for k in (*_EVENT_FIELDS, "url", "offers_hash")}
    return hashlib.sha1(json.dumps(data, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def _note_write(state: ORMExecuteState) -> None:
    if not state.is_select:  # INSERT / UPDATE / DELETE (bulk or Core) or raw SQL
        state.session.info["wrote"] = True


def _note_flush(session: Session, _context: Any) -> None:
    session.info["wrote"] = True  # changed ORM objects


def _count_commit(session: Session) -> None:
    # After the real COMMIT: a reader that sees the new count also sees the new rows.
    if session.info.pop("wrote", False):
        Database.changes += 1


SCHEMA_LOCK_KEY = 0x5307D8  # pg_advisory_lock key of create_schema: any constant, the same in every process


def postgres_url(url: URL) -> URL:
    """``postgres://`` / ``postgresql://`` (what Railway and most hosts hand out) on the psycopg 3 driver."""
    if url.drivername in ("postgres", "postgresql"):
        return url.set(drivername="postgresql+psycopg")
    return url


def _postgres_connect_args(options: DatabaseSettings) -> dict[str, Any]:
    """libpq settings of every connection: a connect timeout, TCP keepalives (a dead peer is noticed in
    seconds, not hours) and server-side timeouts, so one stuck statement or abandoned transaction can
    never hold its locks forever."""
    server = {
        "statement_timeout": options.statement_timeout_ms,
        "idle_in_transaction_session_timeout": options.idle_in_transaction_timeout_ms,
        "lock_timeout": options.lock_timeout_ms,
    }
    return {
        "connect_timeout": options.connect_timeout,
        "application_name": options.application_name,
        "keepalives": 1,
        "keepalives_idle": options.keepalive_idle,
        "keepalives_interval": options.keepalive_interval,
        "keepalives_count": options.keepalive_count,
        "options": " ".join(f"-c {name}={value}" for name, value in server.items()),
    }


class Database:
    # Committed writing transactions in this process (any Database): cached API responses
    # keyed by it go stale the moment data changes.
    changes: ClassVar[int] = 0

    def __init__(self, url: str, echo: bool = False, options: DatabaseSettings | None = None) -> None:
        """``url``: a PostgreSQL URL; empty = the local server (``localpg.py``, localhost and tests).
        ``options``: pool, timeouts and write mode (``database`` settings; default: the loaded ones)."""
        self.options = options = options or get_settings().database
        if not url:
            from partylinks.localpg import local_url  # local: localhost only (pgserver is a dev dependency)

            url = local_url(options)
        self.engine: Engine = create_engine(
            postgres_url(make_url(url)),
            echo=echo,
            pool_size=options.pool_size,
            max_overflow=options.max_overflow,
            pool_timeout=options.pool_timeout,
            pool_recycle=options.pool_recycle,
            pool_pre_ping=True,  # a connection the server or the network dropped is replaced, not used
            pool_use_lifo=True,  # reuse the warmest connections; surplus ones idle out and get recycled
            connect_args=_postgres_connect_args(options),
        )
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)
        sa_event.listen(self._sessions, "do_orm_execute", _note_write)
        sa_event.listen(self._sessions, "after_flush", _note_flush)
        sa_event.listen(self._sessions, "after_commit", _count_commit)
        # Writers run concurrently on row locks; serialize_writes queues this process's writers instead.
        self.serialize_writes = options.serialize_writes
        self._writer = threading.Lock()
        self._writing = threading.local()

    def create_schema(self) -> None:
        """Create missing tables, add columns introduced since the DB was created, then any missing index.

        Safe to run from several processes at once (one at a time, under an advisory lock), and an index that cannot be built (say, duplicates under a new unique one) is logged, never
        fatal: the site still starts.

        ponytail: additive-only auto-migration (new columns, new indexes); switch to Alembic once a change
        needs renames, type changes or data backfills.
        """
        self.wait_until_ready()
        with self._schema_lock():
            Base.metadata.create_all(self.engine)
            existing = inspect(self.engine)
            dialect = self.engine.dialect
            with self.engine.begin() as conn:
                for table in Base.metadata.sorted_tables:
                    present = {c["name"] for c in existing.get_columns(table.name)}
                    for column in table.columns:
                        if column.name in present:
                            continue
                        ddl = column.type.compile(dialect=dialect)
                        default = _literal_default(column, dialect)
                        suffix = f" DEFAULT {default}" if default is not None else ""
                        if default is not None and not column.nullable:
                            suffix += " NOT NULL"  # existing rows take the default (PostgreSQL: no rewrite)
                        conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN "{column.name}" {ddl}{suffix}'))
                        log.info("schema: added column %s.%s", table.name, column.name)
            for table in Base.metadata.sorted_tables:
                for index in table.indexes:  # new indexes, and those of columns added above
                    self._create_index(index)
            self._tune_storage()
            self._create_extensions()

    def wait_until_ready(self) -> None:
        """At startup, wait (with backoff, up to ``database.startup_wait_s``) until PostgreSQL answers:
        a deploy that starts while the database restarts waits for it instead of crashing. Raises the
        last error once the time is up (the deploy's health check then fails and the old one stays)."""
        deadline = time.monotonic() + self.options.startup_wait_s
        delay = 1.0
        while True:
            try:
                with self.engine.connect() as conn:
                    version = conn.scalar(text("SHOW server_version"))
                log.info("database: PostgreSQL %s ready", version)
                return
            except DBAPIError as exc:
                if time.monotonic() + delay > deadline:
                    log.error("database: not ready after %ss: %s", self.options.startup_wait_s, exc.orig)
                    raise
                log.warning("database: not ready (%s), retrying in %.0fs", type(exc.orig).__name__, delay)
                time.sleep(delay)
                delay = min(delay * 2, 10)

    @contextmanager
    def _schema_lock(self) -> Generator[None]:
        """One process changes the schema at a time (a deploy, a CLI command)."""
        with self.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(select(func.pg_advisory_lock(SCHEMA_LOCK_KEY)))
            try:
                yield
            finally:
                conn.execute(select(func.pg_advisory_unlock(SCHEMA_LOCK_KEY)))

    def _create_index(self, index: Index) -> None:
        try:
            with self.engine.begin() as conn:
                index.create(conn, checkfirst=True)
        except DBAPIError as exc:  # e.g. duplicates under a new unique index: fix the data, retried each start
            log.error("schema: could not create index %s: %s", index.name, exc.orig)

    def _tune_storage(self) -> None:
        """Table storage options the ORM cannot declare: room for HOT updates (see ix_events_feed)."""
        tables = {"events": self.options.events_fillfactor, "ticket_tiers": self.options.ticket_tiers_fillfactor}
        with self.engine.begin() as conn:
            for table, fillfactor in tables.items():
                current = conn.scalar(text(f"SELECT reloptions FROM pg_class WHERE oid = '{table}'::regclass")) or []
                if f"fillfactor={fillfactor}" not in current:
                    conn.execute(text(f"ALTER TABLE {table} SET (fillfactor = {fillfactor})"))
                    log.info("schema: %s fillfactor set to %d", table, fillfactor)

    def _create_extensions(self) -> None:
        for name in self.options.extensions:
            try:
                with self.engine.begin() as conn:
                    conn.execute(text(f'CREATE EXTENSION IF NOT EXISTS "{name}"'))
            except DBAPIError as exc:  # not installed, or no privilege: the site runs without it
                log.warning("schema: extension %s not created: %s", name, exc.orig)

    def storage_stats(self) -> dict[str, int]:
        """Size of the database on disk, in bytes (tables, indexes, TOAST; autovacuum reuses free space)."""
        with self.engine.connect() as conn:
            return {"database_bytes": conn.scalar(select(func.pg_database_size(func.current_database()))) or 0}

    def mark_interrupted_runs(self) -> int:
        """Runs still 'running' when the process starts were killed mid-way: record that."""
        with self.session() as session:
            result = session.execute(
                update(ScrapeRunRow)
                .where(ScrapeRunRow.status == "running")
                .values(status="interrupted", error="process stopped before the run finished", finished_at=utcnow())
            )
            count = cast("CursorResult[Any]", result).rowcount or 0
        if count:
            log.warning("marked %d unfinished runs as interrupted", count)
        return count

    def interrupted_sources(self, max_in_a_row: int) -> set[str]:
        """Sources whose latest run was interrupted (a deploy or restart cut it), fewer than
        ``max_in_a_row`` times in a row: a run that keeps killing the process is not resumed again."""
        ranked = select(
            ScrapeRunRow.source,
            ScrapeRunRow.status,
            func.row_number().over(partition_by=ScrapeRunRow.source, order_by=ScrapeRunRow.id.desc()).label("n"),
        ).subquery()
        with self.session(write=False) as session:
            rows = session.execute(
                select(ranked.c.source, ranked.c.status)
                .where(ranked.c.n <= max_in_a_row)
                .order_by(ranked.c.source, ranked.c.n)
            ).all()
        latest: dict[str, list[str]] = defaultdict(list)
        for source, status in rows:
            latest[source].append(status)
        return {
            s
            for s, statuses in latest.items()
            if statuses[0] == "interrupted" and statuses.count("interrupted") < max_in_a_row
        }

    @contextmanager
    def session(self, write: bool = True) -> Generator[Session]:
        """``write=False`` for page requests: they read beside a writer and never queue behind one."""
        if not (write and self.serialize_writes):
            with self._sessions() as session, session.begin():
                yield session
            return
        if getattr(self._writing, "active", False):
            raise RuntimeError("nested write session: the outer one holds the write lock")
        if not self._writer.acquire(timeout=WRITE_WAIT_S):
            raise PoolTimeout(f"no turn in the writer queue within {WRITE_WAIT_S}s")  # transient: retried
        self._writing.active = True
        try:
            with self._sessions() as session, session.begin():
                yield session
        finally:
            self._writing.active = False
            self._writer.release()


# --------------------------------------------------------------------------- writes
_EVENT_FIELDS = (
    "source", "source_event_id", "slug", "venue_slug", "title", "description", "start_at", "end_at", "time_known", "time_zone", "venue_name", "address",
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

    * events  - one ``UPDATE`` of last_seen_at for those unchanged since the last scan (content_hash),
      one multi-row ``INSERT ... ON CONFLICT DO UPDATE`` for new and changed ones
    * tickets - one ``DELETE`` of dropped positions + one multi-row upsert that skips unchanged tiers
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
        row.id: (row.offers_hash, row.offers_changed_at, row.content_hash)
        for row in session.execute(
            select(EventRow.id, EventRow.offers_hash, EventRow.offers_changed_at, EventRow.content_hash).where(
                EventRow.id.in_(ids)
            )
        )
    }
    hashes = {e.event_id: offers_hash(e) for e in events}

    def changed_at(event_id: str) -> datetime | None:
        if event_id not in stored:
            return now  # new event
        old_hash, old_at, _ = stored[event_id]
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
    for row in event_rows:
        row["content_hash"] = content_hash(row)
    seen = {r["id"] for r in event_rows if r["id"] in stored and stored[r["id"]][2] == r["content_hash"]}
    changed = [r for r in event_rows if r["id"] not in seen]
    changes = _deal_changes_for(session, events, hashes, now)
    if seen:  # nothing new on the page: a small update, HOT (no indexed column changes)
        session.execute(
            update(EventRow).where(EventRow.id.in_(sorted(seen))).values(last_seen_at=now, is_listed=True),
            execution_options={"synchronize_session": False},
        )
    if changed:
        session.execute(_upsert(session, EventRow, changed, key="id", keep=_GROUP_COLUMNS | {"id", "first_seen_at"}))
    if changes:
        session.execute(insert(DealChangeRow), changes)

    _write_tiers(session, events)

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


def _write_tiers(session: Session, events: Sequence[ScrapedEvent]) -> None:
    """Tiers by (event, position): a tier is written only when one of its values changed, and the
    positions past an event's new tier count are deleted. Rewriting them all on every scan left
    a dead row per tier and kept autovacuum busy."""
    counts = values(column("event_id", Text), column("tiers", Integer), name="counts").data(
        [(e.event_id, len(e.tickets)) for e in events]
    )
    session.execute(
        delete(TicketRow).where(TicketRow.event_id == counts.c.event_id, TicketRow.position >= counts.c.tiers)
    )
    rows = [
        {"event_id": e.event_id, "position": i, **tier.model_dump(mode="python")}
        for e in sorted(events, key=lambda e: e.event_id)  # consistent lock order across writers
        for i, tier in enumerate(e.tickets)
    ]
    if not rows:
        return
    stmt = pg_insert(TicketRow).values(rows)
    columns = [c for c in rows[0] if c not in ("event_id", "position")]
    table = cast(Table, TicketRow.__table__)
    changed = tuple_(*(table.c[c] for c in columns)).is_distinct_from(tuple_(*(stmt.excluded[c] for c in columns)))
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["event_id", "position"], set_={c: stmt.excluded[c] for c in columns}, where=changed
        )
    )


def bulk_update_by_id(session: Session, model: type[Base], rows: list[dict[str, Any]]) -> None:
    """``UPDATE ... WHERE id = :id`` executemany via Core.

    Unlike ORM bulk update it does not fail when a row was deleted meanwhile (e.g. by
    retention); that row is simply skipped. Rows are sorted by id so concurrent writers
    lock rows in the same order (no deadlocks).
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


def dialect_insert(session: Session, model: type[Base]) -> Any:
    """``INSERT`` with ``ON CONFLICT`` (upserts)."""
    return pg_insert(model)


def _upsert(session: Session, model: type[Base], rows: list[dict[str, Any]], key: str, keep: set[str]) -> Any:
    """Multi-row upsert: every column of ``rows`` but ``keep`` is overwritten on a key conflict."""
    stmt = dialect_insert(session, model).values(rows)
    columns = [c for c in rows[0] if c not in keep]
    return stmt.on_conflict_do_update(index_elements=[key], set_={c: stmt.excluded[c] for c in columns})


def delete_events(session: Session, event_ids: Sequence[str]) -> list[str]:
    """Bulk delete events with their tickets and images. Returns local artwork files to remove."""
    from partylinks.plans.routes import delete_plans  # local: plans.routes imports accounts, which imports db

    files: list[str] = []
    for chunk in _chunks(list(event_ids), 1000):
        files += [p for p in session.scalars(select(ImageRow.local_path).where(ImageRow.event_id.in_(chunk))) if p]
        session.execute(delete(TicketRow).where(TicketRow.event_id.in_(chunk)))
        session.execute(delete(ImageRow).where(ImageRow.event_id.in_(chunk)))
        session.execute(delete(DealChangeRow).where(DealChangeRow.event_id.in_(chunk)))
        delete_plans(session, chunk)  # "who's coming?" plans go with their event
        delete_saved(session, chunk)  # hearts go with their event
        delete_hits(session, chunk)  # alert matches too
        # Through the module: importing artwork first (seo does) reaches here while it is half loaded.
        partylinks.artwork.models.delete_artwork(session, chunk)  # drawn cards of events with no picture
        session.execute(delete(EventRow).where(EventRow.id.in_(chunk)))
    prune_sponsored(session)  # sponsored picks of events now gone
    return files


def _chunks(items: list[str], size: int) -> Iterator[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def start_run(session: Session, source: str, trigger: str) -> ScrapeRunRow:
    run = ScrapeRunRow(source=source, trigger=trigger, status="running", started_at=utcnow())
    session.add(run)
    session.flush()
    return run


# Tables kept in their own packages, imported here so they are on Base before create_schema runs.
import partylinks.accounts.models
import partylinks.ads.models
import partylinks.alerts.models
import partylinks.artistdb
import partylinks.artwork.models
import partylinks.mail.models
import partylinks.plans.models
import partylinks.saved.models
import partylinks.worker.models
from partylinks.ads.models import prune_sponsored
from partylinks.alerts.models import delete_hits
from partylinks.saved.models import delete_saved
