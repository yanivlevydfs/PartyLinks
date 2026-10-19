"""Persistence: SQLAlchemy schema, engine/session factory and repository functions."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    LargeBinary,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    TypeDecorator,
    create_engine,
    delete,
    insert,
    select,
    update,
)
from sqlalchemy import event as sa_event
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from partylinks.config import project_path
from partylinks.models import ScrapedEvent


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
    in_target_city: Mapped[bool | None] = mapped_column(Boolean, index=True)
    genres: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_genres: Mapped[list[str]] = mapped_column(JSON, default=list)
    event_types: Mapped[list[str]] = mapped_column(JSON, default=list)
    artists: Mapped[list[str]] = mapped_column(JSON, default=list)
    lineup: Mapped[list[str]] = mapped_column(JSON, default=list)
    local_date: Mapped[date | None] = mapped_column(Date, index=True)
    day_of_week: Mapped[str | None] = mapped_column(String(16), index=True)
    time_slot: Mapped[str | None] = mapped_column(String(32), index=True)
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
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)

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

    event: Mapped[EventRow] = relationship(back_populates="images")


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
    error: Mapped[str | None] = mapped_column(Text)
    failures: Mapped[list[str]] = mapped_column(JSON, default=list)


def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")  # concurrent readers while a source writes
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


class Database:
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
            connect_args={"timeout": 30, "check_same_thread": False} if is_sqlite else {},
        )
        if is_sqlite:
            sa_event.listen(self.engine, "connect", _sqlite_pragmas)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    def create_schema(self) -> None:
        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self._sessions() as session, session.begin():
            yield session


# --------------------------------------------------------------------------- writes
_EVENT_FIELDS = (
    "source", "source_event_id", "slug", "venue_slug", "title", "description", "start_at", "end_at", "venue_name", "address",
    "city", "latitude", "longitude", "in_target_city", "genres", "source_genres", "event_types", "artists",
    "lineup", "local_date", "day_of_week", "time_slot", "is_weekend", "min_age", "organizer", "image_url",
    "currency", "min_price", "max_price", "has_free_entry", "entry_type", "is_sold_out", "extra",
)  # fmt: skip


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

    event_rows = [
        {"id": e.event_id, "url": str(e.url), "first_seen_at": now, "last_seen_at": now,
         **{f: getattr(e, f) for f in _EVENT_FIELDS}}
        for e in events
    ]  # fmt: skip
    session.execute(_upsert(session, EventRow, event_rows, key="id", keep={"id", "first_seen_at"}))

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
    if moved:
        session.execute(update(ImageRow), moved)
    added = [{"event_id": eid, "url": url, "position": pos} for (eid, url), pos in wanted.items() if (eid, url) not in known]
    if added:
        session.execute(insert(ImageRow), added)
    return len(events), [row.local_path for row in stale if row.local_path]


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
