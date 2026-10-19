"""REST API (FastAPI) + scheduler lifecycle."""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import AsyncGenerator, Callable, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import BaseModel, ConfigDict, computed_field
from sqlalchemy import ColumnElement, String, cast, func, or_, select
from sqlalchemy.orm import Session, selectinload, undefer

from partylinks.config import Settings, get_settings
from partylinks.db import Database, EventRow, ImageRow, ScrapeRunRow
from partylinks.models import EntryType
from partylinks.scheduler import RETENTION_JOB_ID, build_scheduler, next_run_times
from partylinks.scrapers import SCRAPERS, configured_sources
from partylinks.service import ScrapeService, SourceBusyError

# Events that started this long ago still count as "upcoming" (parties run overnight).
UPCOMING_GRACE = timedelta(hours=8)
FACET_LIMIT = 50


# --------------------------------------------------------------------------- schemas
class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class TicketOut(_Out):
    source_ticket_id: str | None
    name: str
    description: str | None
    kind: str
    status: str
    price: float | None
    service_fee: float | None
    total_price: float | None
    original_price: float | None
    price_rounds: list[float]
    currency: str
    admits: int
    is_free: bool
    pay_at_door: bool
    is_sold_out: bool
    is_active: bool
    remaining: int | None
    sale_start: datetime | None
    sale_end: datetime | None


class ImageOut(_Out):
    id: int
    url: str
    content_type: str | None
    size_bytes: int | None
    downloaded_at: datetime | None

    @computed_field
    @property
    def stored_url(self) -> str | None:
        """Our own copy of the artwork, served by this API."""
        return f"/images/{self.id}" if self.downloaded_at else None


class EventSummaryOut(_Out):
    id: str
    slug: str | None
    source: str
    source_event_id: str
    url: str
    title: str
    start_at: datetime | None
    end_at: datetime | None
    local_date: date | None
    day_of_week: str | None
    time_slot: str | None
    is_weekend: bool | None
    venue_name: str | None
    venue_slug: str | None
    address: str | None
    city: str | None
    latitude: float | None
    longitude: float | None
    in_target_city: bool | None
    genres: list[str]
    event_types: list[str]
    lineup: list[str]
    min_age: int | None
    image_url: str | None
    cover_url: str | None
    currency: str
    min_price: float | None
    max_price: float | None
    entry_type: str
    has_free_entry: bool
    is_sold_out: bool
    last_seen_at: datetime


class EventOut(EventSummaryOut):
    description: str | None
    source_genres: list[str]
    artists: list[str]
    organizer: str | None
    extra: dict[str, Any]
    first_seen_at: datetime
    tickets: list[TicketOut]
    images: list[ImageOut]


class EventPage(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[EventSummaryOut]


class RunOut(_Out):
    id: int
    source: str
    trigger: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    events_found: int
    events_saved: int
    events_failed: int
    error: str | None
    failures: list[str]


class SourceOut(BaseModel):
    name: str
    enabled: bool
    cron: str
    running: bool
    next_run_at: datetime | None
    last_run: RunOut | None


class FacetValue(BaseModel):
    value: str
    events: int


class Categories(BaseModel):
    """Counts of upcoming events per category value, for building filters."""

    genres: list[FacetValue]
    dates: list[FacetValue]
    days_of_week: list[FacetValue]
    time_slots: list[FacetValue]
    entry_types: list[FacetValue]
    venues: list[FacetValue]
    cities: list[FacetValue]
    lineup: list[FacetValue]
    event_types: list[FacetValue]
    sources: list[FacetValue]


class RetentionOut(BaseModel):
    removed_events: int


class EventSort(StrEnum):
    start_asc = "start_at"
    start_desc = "-start_at"
    price_asc = "min_price"
    price_desc = "-min_price"
    seen_desc = "-last_seen_at"


_SORT_COLUMNS = {
    EventSort.start_asc: EventRow.start_at.asc(),
    EventSort.start_desc: EventRow.start_at.desc(),
    EventSort.price_asc: EventRow.min_price.asc(),
    EventSort.price_desc: EventRow.min_price.desc(),
    EventSort.seen_desc: EventRow.last_seen_at.desc(),
}


# --------------------------------------------------------------------------- app
class _SiteFiles(StaticFiles):
    """Static files with an SPA fallback: unknown paths get index.html (client-side routes)."""

    async def get_response(self, path: str, scope: Any) -> Response:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != status.HTTP_404_NOT_FOUND:
                raise
            return await super().get_response("index.html", scope)


def create_app(settings: Settings | None = None, start_scheduler: bool | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        db = Database(settings.database.url, settings.database.echo)
        db.create_schema()
        service = ScrapeService(settings, db)
        run_scheduler = settings.scheduler.enabled if start_scheduler is None else start_scheduler
        scheduler = build_scheduler(settings, service) if run_scheduler else None
        app.state.settings, app.state.db, app.state.service, app.state.scheduler = settings, db, service, scheduler
        app.state.tasks = set()
        if scheduler:
            scheduler.start()
        try:
            yield
        finally:
            if scheduler:
                scheduler.shutdown(wait=False)
            for task in app.state.tasks:
                task.cancel()
            service.shutdown()
            db.engine.dispose()

    app = FastAPI(
        title="PartyLinks API",
        version="0.1.0",
        description="Tel Aviv party events scraped from Israeli ticketing sites.",
        lifespan=lifespan,
    )
    _register_routes(app)
    # The React site (web/), once built with `npm run build`. Mounted last so API routes win.
    site = Path(__file__).resolve().parents[2] / "web" / "dist"
    if site.is_dir():
        app.mount("/", _SiteFiles(directory=site, html=True), name="site")
    return app


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.db.session() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def _upcoming_filter() -> ColumnElement[bool]:
    now = datetime.now(UTC)
    return or_(EventRow.end_at >= now, EventRow.start_at >= now - UPCOMING_GRACE)


def _json_contains(column: Any, values: list[str]) -> ColumnElement[bool]:
    """Any-of match on a JSON string array (portable: SQLite and PostgreSQL)."""
    return or_(*(cast(column, String).ilike(f'%"{v}"%') for v in values))


def _register_routes(app: FastAPI) -> None:  # noqa: C901 - flat list of route handlers
    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/events", response_model=EventPage, tags=["events"])
    def list_events(
        request: Request,
        session: SessionDep,
        source: Annotated[list[str] | None, Query(description="Source name (repeatable)")] = None,
        genre: Annotated[list[str] | None, Query(description="Canonical genre, any-of (repeatable)")] = None,
        artist: Annotated[str | None, Query(min_length=2, description="DJ / performer in the lineup")] = None,
        venue: Annotated[str | None, Query(min_length=2, description="Venue name contains")] = None,
        venue_slug: Annotated[str | None, Query(description="Exact venue slug")] = None,
        city: Annotated[str | None, Query(min_length=2, description="City contains")] = None,
        in_target_city: Annotated[bool | None, Query(description="Matched to the configured target city")] = None,
        entry_type: Annotated[list[EntryType] | None, Query(description="free / paid / free_and_paid")] = None,
        free_entry: Annotated[bool | None, Query(description="Has at least one free ticket tier")] = None,
        sold_out: bool | None = None,
        day_of_week: Annotated[list[str] | None, Query(description="e.g. thursday (repeatable)")] = None,
        time_slot: Annotated[list[str] | None, Query(description="e.g. night (repeatable)")] = None,
        weekend: bool | None = None,
        on_date: Annotated[date | None, Query(alias="date", description="Local start date")] = None,
        upcoming: Annotated[bool, Query(description="Hide events that already ended")] = True,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        min_price: Annotated[float | None, Query(ge=0)] = None,
        max_price: Annotated[float | None, Query(ge=0)] = None,
        q: Annotated[str | None, Query(min_length=2, description="Search title/description/venue/lineup")] = None,
        sort: EventSort = EventSort.start_asc,
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int | None, Query(ge=1)] = None,
    ) -> EventPage:
        cfg: Settings = request.app.state.settings
        size = min(page_size or cfg.api.default_page_size, cfg.api.max_page_size)
        tz = ZoneInfo(cfg.timezone)
        filters: list[ColumnElement[bool]] = []
        if source:
            filters.append(EventRow.source.in_(source))
        if genre:
            filters.append(_json_contains(EventRow.genres, genre))
        if artist:
            filters.append(cast(EventRow.lineup, String).ilike(f"%{artist}%"))
        if venue:
            filters.append(EventRow.venue_name.ilike(f"%{venue}%"))
        if venue_slug:
            filters.append(EventRow.venue_slug == venue_slug)
        if city:
            filters.append(or_(EventRow.city.ilike(f"%{city}%"), EventRow.address.ilike(f"%{city}%")))
        if in_target_city is not None:
            filters.append(EventRow.in_target_city.is_(in_target_city))
        if entry_type:
            filters.append(EventRow.entry_type.in_([e.value for e in entry_type]))
        if free_entry is not None:
            filters.append(EventRow.has_free_entry.is_(free_entry))
        if sold_out is not None:
            filters.append(EventRow.is_sold_out.is_(sold_out))
        if day_of_week:
            filters.append(EventRow.day_of_week.in_([d.lower() for d in day_of_week]))
        if time_slot:
            filters.append(EventRow.time_slot.in_(time_slot))
        if weekend is not None:
            filters.append(EventRow.is_weekend.is_(weekend))
        if on_date:
            filters.append(EventRow.local_date == on_date)
        if upcoming:
            filters.append(_upcoming_filter())
        if date_from:
            filters.append(EventRow.start_at >= (date_from if date_from.tzinfo else date_from.replace(tzinfo=tz)))
        if date_to:
            filters.append(EventRow.start_at <= (date_to if date_to.tzinfo else date_to.replace(tzinfo=tz)))
        if min_price is not None:
            filters.append(EventRow.max_price >= min_price)
        if max_price is not None:
            filters.append(EventRow.min_price <= max_price)
        if q:
            pattern = f"%{q}%"
            filters.append(
                or_(
                    EventRow.title.ilike(pattern),
                    EventRow.description.ilike(pattern),
                    EventRow.venue_name.ilike(pattern),
                    cast(EventRow.lineup, String).ilike(pattern),
                )
            )
        total = session.scalar(select(func.count()).select_from(EventRow).where(*filters)) or 0
        rows = session.scalars(
            select(EventRow)
            .where(*filters)
            .order_by(_SORT_COLUMNS[sort], EventRow.id)
            .offset((page - 1) * size)
            .limit(size)
            .options(selectinload(EventRow.images))
        ).all()
        return EventPage(
            total=total, page=page, page_size=size, items=[EventSummaryOut.model_validate(r) for r in rows]
        )

    @app.get("/events/{event_ref}", response_model=EventOut, tags=["events"])
    def get_event(event_ref: str, session: SessionDep) -> EventOut:
        """Look an event up by id (``source:source_event_id``) or by slug."""
        row = session.scalar(
            select(EventRow)
            .where(or_(EventRow.id == event_ref, EventRow.slug == event_ref))
            .options(selectinload(EventRow.tickets), selectinload(EventRow.images))
        )
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found")
        return EventOut.model_validate(row)

    @app.get("/images/{image_id}", tags=["events"], response_class=Response)
    def get_image(image_id: int, request: Request, session: SessionDep) -> Response:
        image = session.scalar(select(ImageRow).where(ImageRow.id == image_id).options(undefer(ImageRow.data)))
        if image is None or image.downloaded_at is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "image not stored")
        media_type = image.content_type or "application/octet-stream"
        headers = {"Cache-Control": "public, max-age=86400"}
        if image.data is not None:
            return Response(content=image.data, media_type=media_type, headers=headers)
        path = request.app.state.service.media.path_for(image.local_path or "")
        if not image.local_path or not path.is_file():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "image file missing")
        return FileResponse(path, media_type=media_type, headers=headers)

    @app.get("/categories", response_model=Categories, tags=["events"])
    def categories(session: SessionDep, upcoming: bool = True) -> Categories:
        query = select(EventRow)
        if upcoming:
            query = query.where(_upcoming_filter())
        rows = session.scalars(query).all()

        def facet(values: Callable[[EventRow], list[Any]], limit: int | None = FACET_LIMIT) -> list[FacetValue]:
            counts = Counter(str(v) for row in rows for v in values(row) if v not in (None, ""))
            return [FacetValue(value=v, events=n) for v, n in counts.most_common(limit)]

        return Categories(
            genres=facet(lambda r: r.genres or [], None),
            dates=sorted(facet(lambda r: [r.local_date], None), key=lambda f: f.value),
            days_of_week=facet(lambda r: [r.day_of_week], None),
            time_slots=facet(lambda r: [r.time_slot], None),
            entry_types=facet(lambda r: [r.entry_type], None),
            venues=facet(lambda r: [r.venue_name]),
            cities=facet(lambda r: [r.city]),
            lineup=facet(lambda r: r.lineup or []),
            event_types=facet(lambda r: r.event_types or []),
            sources=facet(lambda r: [r.source], None),
        )

    @app.get("/sources", response_model=list[SourceOut], tags=["sources"])
    def list_sources(request: Request, session: SessionDep) -> list[SourceOut]:
        service: ScrapeService = request.app.state.service
        next_runs = next_run_times(request.app.state.scheduler)
        result = []
        for name, cfg in configured_sources(request.app.state.settings).items():
            last = session.scalar(
                select(ScrapeRunRow).where(ScrapeRunRow.source == name).order_by(ScrapeRunRow.id.desc()).limit(1)
            )
            result.append(
                SourceOut(
                    name=name,
                    enabled=cfg.enabled,
                    cron=cfg.cron,
                    running=service.is_running(name),
                    next_run_at=next_runs.get(name),
                    last_run=RunOut.model_validate(last) if last else None,
                )
            )
        return result

    @app.post("/sources/{name}/run", status_code=status.HTTP_202_ACCEPTED, tags=["sources"])
    async def trigger_source(name: str, request: Request) -> dict[str, str]:
        if name not in SCRAPERS or name not in request.app.state.settings.sources:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown source {name!r}")
        service: ScrapeService = request.app.state.service
        if service.is_running(name):
            raise HTTPException(status.HTTP_409_CONFLICT, f"{name} is already running")

        async def run() -> None:
            try:
                await service.run_source(name, trigger="api")
            except SourceBusyError:
                pass

        task = asyncio.create_task(run())
        request.app.state.tasks.add(task)
        task.add_done_callback(request.app.state.tasks.discard)
        return {"status": "started", "source": name}

    @app.get("/runs", response_model=list[RunOut], tags=["sources"])
    def list_runs(
        session: SessionDep,
        source: str | None = None,
        run_status: Annotated[str | None, Query(alias="status")] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
    ) -> list[RunOut]:
        query = select(ScrapeRunRow).order_by(ScrapeRunRow.id.desc()).limit(limit)
        if source:
            query = query.where(ScrapeRunRow.source == source)
        if run_status:
            query = query.where(ScrapeRunRow.status == run_status)
        return [RunOut.model_validate(r) for r in session.scalars(query)]

    @app.post("/maintenance/retention", response_model=RetentionOut, tags=["maintenance"])
    async def run_retention(request: Request) -> RetentionOut:
        return RetentionOut(removed_events=await request.app.state.service.purge_expired())

    @app.get("/maintenance/retention", tags=["maintenance"])
    def retention_status(request: Request) -> dict[str, Any]:
        cfg: Settings = request.app.state.settings
        return {
            **cfg.retention.model_dump(),
            "next_run_at": next_run_times(request.app.state.scheduler).get(RETENTION_JOB_ID),
        }
