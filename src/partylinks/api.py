"""REST API (FastAPI) + scheduler lifecycle."""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import AsyncGenerator, Callable, Iterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator
from sqlalchemy import ColumnElement, String, and_, cast, func, not_, or_, select
from sqlalchemy.orm import Session, selectinload, undefer

from partylinks import seo
from partylinks.admin import AdminDep
from partylinks.admin import router as admin_router
from partylinks.config import Settings, get_settings
from partylinks.db import Database, DealChangeRow, EventRow, ImageRow, ScrapeRunRow, upcoming_filter
from partylinks.logs import setup_logging
from partylinks.models import EntryType
from partylinks.scheduler import RETENTION_JOB_ID, build_scheduler, next_run_times
from partylinks.scrapers import SCRAPERS, configured_sources, source_errors
from partylinks.security import SecurityMiddleware
from partylinks.service import ScrapeService, SourceBusyError

FACET_LIMIT = 1000  # venue / artist lists on the site are searched client-side


# --------------------------------------------------------------------------- schemas
class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class TicketOut(_Out):
    """A ticket tier as it stands NOW: a sale window that closed (or has not opened)
    since the last scan is reflected immediately, without waiting for the next scan."""

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

    @model_validator(mode="after")
    def _live_status(self) -> TicketOut:
        now = datetime.now(UTC)
        if self.status == "on_sale" and self.sale_end is not None and _utc(self.sale_end) <= now:
            self.status, self.is_active = "closed", False
        elif self.status == "on_sale" and self.sale_start is not None and _utc(self.sale_start) > now:
            self.status, self.is_active = "coming_soon", False
        return self


class DealChangeOut(_Out):
    """A change to a deal detected between two scans."""

    event_id: str
    source: str
    at: datetime
    kind: str
    old: str | None
    new: str | None
    detail: str | None


class ChangeFeedItem(DealChangeOut):
    title: str
    slug: str | None
    url: str
    start_at: datetime | None


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
    area: str | None
    is_weekend: bool | None
    venue_name: str | None
    venue_slug: str | None
    address: str | None
    city: str | None
    latitude: float | None
    longitude: float | None
    country: str | None
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
    free_sold_out: bool
    last_seen_at: datetime
    first_seen_at: datetime
    # False when the source stopped listing it (cancelled / pulled); hidden from upcoming lists.
    is_listed: bool = True
    # Last time tickets / prices / free entry / sold-out changed (first seen counts as a change).
    offers_changed_at: datetime | None = None
    # Duplicate listings of the same event (other promoters / platforms) share a group.
    # The group_* fields cover every listing, so a free / cheaper offer elsewhere is never hidden.
    group_id: str | None
    listings: int = Field(1, validation_alias="group_size")
    group_min_price: float | None
    group_max_price: float | None
    group_has_free_entry: bool
    group_free_entry_until: datetime | None = None
    group_entry_type: str
    best_deal: DealOut | None = None
    # Every ticket site selling this event (this listing's first, then the rest of its group).
    sources: list[str] = []


class DealOut(_Out):
    """The cheapest listing of an event across all sources (free entry wins)."""

    id: str
    source: str
    url: str
    min_price: float | None
    has_free_entry: bool
    entry_type: str


class AlternativeOut(_Out):
    """Another listing of the same event: different seller, link, prices and tiers."""

    id: str
    slug: str | None
    source: str
    url: str
    title: str
    start_at: datetime | None
    min_price: float | None
    max_price: float | None
    entry_type: str
    has_free_entry: bool
    is_sold_out: bool
    tickets: list[TicketOut]


class EventOut(EventSummaryOut):
    description: str | None
    source_genres: list[str]
    artists: list[str]
    organizer: str | None
    extra: dict[str, Any]
    tickets: list[TicketOut]
    images: list[ImageOut]
    alternatives: list[AlternativeOut] = []
    # Recent deal changes (newest first): price moves, free entry ending, tiers added...
    changes: list[DealChangeOut] = []


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
    events_changed: int | None = None
    events_unlisted: int | None = None
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
    # Venues: the area of the city the venue is in. Cities: the region of the country.
    area: str | None = None
    # Artists only, when a ticket site curates the artist (see artists.py): verified profile.
    verified: bool | None = None
    image_url: str | None = None
    kind: str | None = None
    genre: str | None = None
    spotify_url: str | None = None
    profile_url: str | None = None
    links: list[str] | None = None


class Categories(BaseModel):
    """Counts of upcoming events per category value, for building filters."""

    genres: list[FacetValue]
    dates: list[FacetValue]
    days_of_week: list[FacetValue]
    time_slots: list[FacetValue]
    areas: list[FacetValue]
    # Regions of the country: "tel_aviv" (the target city) plus the configured region keys.
    regions: list[FacetValue]
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
    EventSort.price_asc: EventRow.group_min_price.asc(),
    EventSort.price_desc: EventRow.group_min_price.desc(),
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
            response = await super().get_response("index.html", scope)
            response.status_code = status.HTTP_404_NOT_FOUND  # client renders "not found"; crawlers see 404
            return response


def create_app(settings: Settings | None = None, start_scheduler: bool | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.logging)  # also for `serve --reload` workers, which never run cli.main()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        db = Database(settings.database.url, settings.database.echo)
        db.create_schema()
        service = ScrapeService(settings, db)
        service.recompute_areas()  # stored events follow the configured areas
        run_scheduler = settings.scheduler.enabled if start_scheduler is None else start_scheduler
        scheduler = build_scheduler(settings, service) if run_scheduler else None
        app.state.settings, app.state.db, app.state.service, app.state.scheduler = settings, db, service, scheduler
        app.state.tasks = set()
        # Map coordinates for address-only events; network lookups run in the background.
        geocoding = asyncio.create_task(asyncio.to_thread(service.geocode))
        app.state.tasks.add(geocoding)
        geocoding.add_done_callback(app.state.tasks.discard)
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

    docs = settings.security.api_docs
    app = FastAPI(
        title="Pulse API",
        version="0.1.0",
        description="Party events across Israel scraped from Israeli ticketing sites.",
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.state.settings = settings  # read by SecurityMiddleware before the lifespan finishes too
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.add_middleware(SecurityMiddleware)  # outermost: rejects before any work is done
    _register_routes(app)
    app.include_router(admin_router)
    # The React site (web/), once built with `npm run build`. Mounted last so API routes win.
    site = Path(__file__).resolve().parents[2] / "web" / "dist"
    seo.register(app, site)  # server-rendered pages + sitemap/robots/llms.txt, before the static catch-all
    if site.is_dir():
        app.mount("/", _SiteFiles(directory=site, html=True), name="site")
    return app


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.db.session() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def _best_deals(session: Session, rows: Sequence[EventRow]) -> dict[str, DealOut]:
    """Cheapest listing per event, loaded in one query for a whole page."""
    ids = {r.best_listing_id for r in rows if r.best_listing_id}
    if not ids:
        return {}
    return {d.id: DealOut.model_validate(d) for d in session.scalars(select(EventRow).where(EventRow.id.in_(ids)))}


def _group_sources(session: Session, rows: Sequence[EventRow]) -> dict[str, list[str]]:
    """Ticket sites per duplicate group, loaded in one query for a whole page."""
    groups = {r.group_id for r in rows if r.group_id}
    found: dict[str, set[str]] = {}
    if groups:
        for group_id, source in session.execute(
            select(EventRow.group_id, EventRow.source).where(EventRow.group_id.in_(groups)).distinct()
        ):
            found.setdefault(group_id, set()).add(source)
    return {
        r.id: [r.source, *sorted(found.get(r.group_id or "", set()) - {r.source})]
        for r in rows
    }


def _summary_extras(session: Session, rows: Sequence[EventRow]) -> dict[str, dict[str, Any]]:
    deals = _best_deals(session, rows)
    sources = _group_sources(session, rows)
    return {
        r.id: {"best_deal": deals.get(r.best_listing_id) if r.best_listing_id else None, "sources": sources[r.id]}
        for r in rows
    }


log = logging.getLogger(__name__)

MAX_LIST_PARAMS = 20
EVENT_CHANGES_LIMIT = 20


def current_clause_for_feed() -> ColumnElement[bool]:
    """The change feed covers current, still-listed events (a delisting is itself a change)."""
    return or_(upcoming_filter(), and_(EventRow.is_listed.is_(False), EventRow.start_at >= datetime.now(UTC)))


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)
MAX_TEXT_PARAM = 100


def _contains(value: str) -> str:
    """LIKE pattern for a user-supplied substring with % and _ taken literally."""
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _aware_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _json_contains(column: Any, values: list[str]) -> ColumnElement[bool]:
    """Any-of match on a JSON string array (portable: SQLite and PostgreSQL)."""
    return or_(*(cast(column, String).ilike(f'%"{v}"%') for v in values))


def _register_routes(app: FastAPI) -> None:  # noqa: C901 - flat list of route handlers
    @app.get("/site", tags=["meta"])
    def site_info(request: Request) -> dict[str, Any]:
        """Public site details the website shows (name, accessibility and privacy contacts)."""
        site = request.app.state.settings.site
        return {
            "name": site.name,
            "base_url": site.base_url,
            "accessibility": site.accessibility.model_dump(mode="json"),
            "privacy": site.privacy.model_dump(mode="json"),
        }

    @app.get("/health", tags=["meta"])
    def health(request: Request, response: Response) -> dict[str, Any]:
        """Liveness + readiness: DB reachable, scheduler alive, per-source freshness, config errors."""
        state = request.app.state
        cfg: Settings = state.settings
        report: dict[str, Any] = {"status": "ok", "checks": {}}
        try:
            with state.db.session() as session:
                session.execute(select(1))
                last_ok = dict(
                    session.execute(
                        select(ScrapeRunRow.source, func.max(ScrapeRunRow.finished_at))
                        .where(ScrapeRunRow.status.in_(("success", "partial")))
                        .group_by(ScrapeRunRow.source)
                    ).all()
                )
            report["checks"]["database"] = "ok"
        except Exception as exc:  # noqa: BLE001 - health must answer, not raise
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            report["status"] = "down"
            report["checks"]["database"] = f"error: {type(exc).__name__}"
            return report
        scheduler = state.scheduler
        report["checks"]["scheduler"] = "disabled" if scheduler is None else ("running" if scheduler.running else "stopped")
        stale_after = timedelta(hours=cfg.scheduler.stale_after_hours)
        now = datetime.now(UTC)
        stale = {
            name: (last_ok[name].isoformat() if last_ok.get(name) else None)
            for name, src in configured_sources(cfg).items()
            if src.enabled and (last_ok.get(name) is None or now - _aware_utc(last_ok[name]) > stale_after)
        }
        report["checks"]["stale_sources"] = stale
        report["checks"]["config_errors"] = source_errors(cfg)
        retention = state.service.retention_state
        report["checks"]["retention"] = {
            "last_success_at": retention.last_success_at,
            "last_error": retention.last_error,
        }
        if (scheduler is not None and not scheduler.running) or report["checks"]["config_errors"] or retention.last_error:
            report["status"] = "degraded"
        return report

    @app.get("/events", response_model=EventPage, tags=["events"])
    def list_events(
        request: Request,
        session: SessionDep,
        source: Annotated[list[str] | None, Query(max_length=MAX_LIST_PARAMS, description="Source name (repeatable)")] = None,
        genre: Annotated[list[str] | None, Query(max_length=MAX_LIST_PARAMS, description="Canonical genre, any-of (repeatable)")] = None,
        artist: Annotated[str | None, Query(min_length=2, max_length=MAX_TEXT_PARAM, description="DJ / performer in the lineup")] = None,
        venue: Annotated[str | None, Query(min_length=2, max_length=MAX_TEXT_PARAM, description="Venue name contains")] = None,
        venue_slug: Annotated[str | None, Query(description="Exact venue slug")] = None,
        city: Annotated[str | None, Query(min_length=2, max_length=MAX_TEXT_PARAM, description="City contains")] = None,
        in_target_city: Annotated[bool | None, Query(description="Matched to the configured target city")] = None,
        abroad: Annotated[bool, Query(description="Events abroad instead of events in Israel")] = False,
        entry_type: Annotated[list[EntryType] | None, Query(max_length=MAX_LIST_PARAMS, description="free / paid / free_and_paid")] = None,
        free_entry: Annotated[bool | None, Query(description="Has at least one free ticket tier")] = None,
        sold_out: bool | None = None,
        day_of_week: Annotated[list[str] | None, Query(max_length=MAX_LIST_PARAMS, description="e.g. thursday (repeatable)")] = None,
        time_slot: Annotated[list[str] | None, Query(max_length=MAX_LIST_PARAMS, description="e.g. night (repeatable)")] = None,
        area: Annotated[list[str] | None, Query(max_length=MAX_LIST_PARAMS, description="Area of the city, e.g. south (repeatable)")] = None,
        weekend: bool | None = None,
        on_date: Annotated[date | None, Query(alias="date", description="Local start date")] = None,
        upcoming: Annotated[bool, Query(description="Hide events that already ended")] = True,
        include_duplicates: Annotated[
            bool, Query(description="Also return non-primary listings of the same event")
        ] = False,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        min_price: Annotated[float | None, Query(ge=0)] = None,
        max_price: Annotated[float | None, Query(ge=0)] = None,
        q: Annotated[str | None, Query(min_length=2, max_length=MAX_TEXT_PARAM, description="Search title/description/venue/lineup")] = None,
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
            filters.append(cast(EventRow.lineup, String).ilike(_contains(artist), escape="\\"))
        if venue:
            filters.append(EventRow.venue_name.ilike(_contains(venue), escape="\\"))
        if venue_slug:
            filters.append(EventRow.venue_slug == venue_slug)
        if city:
            filters.append(
                or_(EventRow.city.ilike(_contains(city), escape="\\"), EventRow.address.ilike(_contains(city), escape="\\"))
            )
        if in_target_city is not None:
            filters.append(EventRow.in_target_city.is_(in_target_city))
        filters.append(EventRow.country.is_not(None) if abroad else EventRow.country.is_(None))
        if entry_type:
            filters.append(EventRow.group_entry_type.in_([e.value for e in entry_type]))
        if free_entry is not None:
            free_now = and_(
                EventRow.group_has_free_entry.is_(True),
                or_(EventRow.group_free_entry_until.is_(None), EventRow.group_free_entry_until > datetime.now(UTC)),
            )
            filters.append(free_now if free_entry else not_(free_now))
        if sold_out is not None:
            filters.append(EventRow.is_sold_out.is_(sold_out))
        if day_of_week:
            filters.append(EventRow.day_of_week.in_([d.lower() for d in day_of_week]))
        if time_slot:
            filters.append(EventRow.time_slot.in_(time_slot))
        if area:
            filters.append(EventRow.area.in_(area))
        if weekend is not None:
            filters.append(EventRow.is_weekend.is_(weekend))
        if on_date:
            filters.append(EventRow.local_date == on_date)
        if upcoming:
            filters.append(upcoming_filter())
        if not include_duplicates:
            filters.append(EventRow.is_primary.is_(True))
        if date_from:
            filters.append(EventRow.start_at >= (date_from if date_from.tzinfo else date_from.replace(tzinfo=tz)))
        if date_to:
            filters.append(EventRow.start_at <= (date_to if date_to.tzinfo else date_to.replace(tzinfo=tz)))
        if min_price is not None:
            filters.append(EventRow.group_max_price >= min_price)
        if max_price is not None:
            filters.append(EventRow.group_min_price <= max_price)
        if q:
            pattern = _contains(q)
            filters.append(
                or_(
                    EventRow.title.ilike(pattern, escape="\\"),
                    EventRow.description.ilike(pattern, escape="\\"),
                    EventRow.venue_name.ilike(pattern, escape="\\"),
                    cast(EventRow.lineup, String).ilike(pattern, escape="\\"),
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
        extras = _summary_extras(session, rows)
        items = [EventSummaryOut.model_validate(r).model_copy(update=extras[r.id]) for r in rows]
        return EventPage(total=total, page=page, page_size=size, items=items)

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
        alternatives = (
            session.scalars(
                select(EventRow)
                .where(EventRow.group_id == row.group_id, EventRow.id != row.id)
                .order_by(EventRow.min_price.asc(), EventRow.id)
                .options(selectinload(EventRow.tickets))
            ).all()
            if row.group_id
            else []
        )
        listing_ids = [row.id, *(a.id for a in alternatives)]  # changes of every listing of the event
        changes = session.scalars(
            select(DealChangeRow)
            .where(DealChangeRow.event_id.in_(listing_ids))
            .order_by(DealChangeRow.at.desc(), DealChangeRow.id.desc())
            .limit(EVENT_CHANGES_LIMIT)
        ).all()
        return EventOut.model_validate(row).model_copy(
            update={
                "alternatives": [AlternativeOut.model_validate(a) for a in alternatives],
                "changes": [DealChangeOut.model_validate(c) for c in changes],
                **_summary_extras(session, [row])[row.id],
            }
        )

    @app.get("/changes", response_model=list[ChangeFeedItem], tags=["events"])
    def deal_changes_feed(
        session: SessionDep,
        since: Annotated[datetime | None, Query(description="Default: last 24 hours")] = None,
        kind: Annotated[list[str] | None, Query(max_length=MAX_LIST_PARAMS, description="e.g. price_down (repeatable)")] = None,
        source: Annotated[list[str] | None, Query(max_length=MAX_LIST_PARAMS)] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> list[ChangeFeedItem]:
        """Deal changes across current events, newest first: price drops, free entry ending, new tiers..."""
        since = _aware_utc(since) if since else datetime.now(UTC) - timedelta(hours=24)
        query = (
            select(DealChangeRow, EventRow.title, EventRow.slug, EventRow.url, EventRow.start_at)
            .join(EventRow, EventRow.id == DealChangeRow.event_id)
            .where(DealChangeRow.at >= since, current_clause_for_feed())
            .order_by(DealChangeRow.at.desc(), DealChangeRow.id.desc())
            .limit(limit)
        )
        if kind:
            query = query.where(DealChangeRow.kind.in_(kind))
        if source:
            query = query.where(DealChangeRow.source.in_(source))
        return [
            ChangeFeedItem.model_validate(
                {**DealChangeOut.model_validate(c).model_dump(), "title": t, "slug": sl, "url": u, "start_at": st}
            )
            for c, t, sl, u, st in session.execute(query).all()
        ]

    @app.get("/images/{image_id}", tags=["events"], response_class=Response)
    def get_image(image_id: int, request: Request, session: SessionDep) -> Response:
        meta = session.execute(
            select(ImageRow.downloaded_at, ImageRow.content_type).where(ImageRow.id == image_id)
        ).first()
        if meta is None or meta.downloaded_at is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "image not stored")
        etag = f'"{image_id}-{int(meta.downloaded_at.timestamp())}"'
        headers = {"Cache-Control": "public, max-age=86400", "ETag": etag}
        if request.headers.get("if-none-match") == etag:  # client already has it: no blob read
            return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
        image = session.scalar(select(ImageRow).where(ImageRow.id == image_id).options(undefer(ImageRow.data)))
        if image is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "image not stored")
        media_type = meta.content_type or "application/octet-stream"
        if image.data is not None:
            return Response(content=image.data, media_type=media_type, headers=headers)
        path = request.app.state.service.media.path_for(image.local_path or "")
        if not image.local_path or not path.is_file():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "image file missing")
        return FileResponse(path, media_type=media_type, headers=headers)

    @app.get("/categories", response_model=Categories, response_model_exclude_none=True, tags=["events"])
    def categories(request: Request, session: SessionDep, upcoming: bool = True) -> Categories:
        # Only the facet columns (not descriptions/extra JSON); each real event counted once.
        query = select(
            EventRow.genres, EventRow.local_date, EventRow.day_of_week, EventRow.time_slot, EventRow.area,
            EventRow.group_entry_type, EventRow.venue_name, EventRow.city, EventRow.lineup, EventRow.event_types,
            EventRow.source, EventRow.in_target_city,
        ).where(EventRow.is_primary.is_(True), EventRow.country.is_(None))  # fmt: skip
        if upcoming:
            query = query.where(upcoming_filter())
        rows = session.execute(query).all()

        def facet(values: Callable[[Any], list[Any]], limit: int | None = FACET_LIMIT) -> list[FacetValue]:
            counts = Counter(str(v) for row in rows for v in values(row) if v not in (None, ""))
            return [FacetValue(value=v, events=n) for v, n in counts.most_common(limit)]

        def artist_facet() -> list[FacetValue]:
            # "Pettra" / "PETTRA" are one artist: merge case-insensitively, show the most used spelling.
            spellings = Counter(str(v) for row in rows for v in row.lineup or [] if v)
            merged: dict[str, tuple[str, int]] = {}
            for name, n in spellings.most_common():
                shown, total = merged.get(name.casefold(), (name, 0))
                merged[name.casefold()] = (shown, total + n)
            ranked = sorted(merged.values(), key=lambda item: -item[1])[:FACET_LIMIT]
            profiles = request.app.state.service.artists.profiles()
            out = []
            for name, n in ranked:
                if p := profiles.get(name.casefold()):
                    out.append(FacetValue(
                        value=p.name, events=n, verified=True, image_url=p.image_url, kind=p.kind, genre=p.genre,
                        spotify_url=p.spotify_url, profile_url=p.profile_url, links=p.links or None,
                    ))  # fmt: skip
                else:
                    out.append(FacetValue(value=name, events=n))
            return out

        venue_area = {r.venue_name: r.area for r in rows if r.venue_name and r.area}
        region_keys = request.app.state.service.enricher.areas.region_keys

        def region(r: Any) -> str | None:
            return "tel_aviv" if r.in_target_city else r.area if r.area in region_keys else None

        city_regions = Counter((r.city, reg) for r in rows if r.city and (reg := region(r)))
        city_region: dict[str, str] = {}
        for (city, reg), _ in city_regions.most_common():
            city_region.setdefault(city, reg)  # a city's most common region
        return Categories(
            genres=facet(lambda r: r.genres or [], None),
            dates=sorted(facet(lambda r: [r.local_date], None), key=lambda f: f.value),
            days_of_week=facet(lambda r: [r.day_of_week], None),
            time_slots=facet(lambda r: [r.time_slot], None),
            areas=facet(lambda r: [r.area], None),
            regions=facet(lambda r: [region(r)], None),
            entry_types=facet(lambda r: [r.group_entry_type], None),
            venues=[f.model_copy(update={"area": venue_area.get(f.value)}) for f in facet(lambda r: [r.venue_name])],
            cities=[
                f.model_copy(update={"area": city_region.get(f.value)})
                for f in facet(lambda r: [r.city])
            ],
            lineup=artist_facet(),
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

    @app.post("/sources/{name}/run", status_code=status.HTTP_202_ACCEPTED, tags=["sources"], dependencies=[AdminDep])
    async def trigger_source(name: str, request: Request) -> dict[str, str]:
        if name not in SCRAPERS or name not in request.app.state.settings.sources:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown source {name!r}")
        service: ScrapeService = request.app.state.service
        try:  # claim the source now, so "started" is only returned when it really started
            future = service.start_source(name, trigger="api")
        except SourceBusyError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

        async def run() -> None:
            try:
                await asyncio.wrap_future(future)
            except Exception:  # noqa: BLE001 - surfaced in logs; the run row records the outcome
                log.exception("API-triggered run of %s failed", name)

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

    @app.post("/maintenance/retention", response_model=RetentionOut, tags=["maintenance"], dependencies=[AdminDep])
    async def run_retention(request: Request) -> RetentionOut:
        try:
            return RetentionOut(removed_events=await request.app.state.service.purge_expired())
        except SourceBusyError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    @app.get("/maintenance/retention", tags=["maintenance"])
    def retention_status(request: Request) -> dict[str, Any]:
        cfg: Settings = request.app.state.settings
        state = request.app.state.service.retention_state
        return {
            **cfg.retention.model_dump(),
            "next_run_at": next_run_times(request.app.state.scheduler).get(RETENTION_JOB_ID),
            "running": request.app.state.service.retention_running,
            "last_run_at": state.last_run_at,
            "last_success_at": state.last_success_at,
            "last_removed": state.last_removed,
            "last_error": state.last_error,
        }
