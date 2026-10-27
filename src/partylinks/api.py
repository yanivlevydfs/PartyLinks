"""REST API (FastAPI) + scheduler lifecycle."""

from __future__ import annotations

import asyncio
import base64
import logging
import threading
import time
from collections import Counter
from collections.abc import AsyncGenerator, Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator
from sqlalchemy import ColumnElement, String, and_, cast, func, not_, or_, select
from sqlalchemy.orm import Session, aliased, defer, load_only, selectinload, undefer
from starlette.exceptions import HTTPException as StarletteHTTPException

from partylinks import clicks, seo, utm
from partylinks import status as server_status
from partylinks.admin import AdminDep, require_admin
from partylinks.admin import router as admin_router
from partylinks.config import Settings, get_settings
from partylinks.db import (
    Database,
    DealChangeRow,
    EventRow,
    ImageRow,
    ScrapeRunRow,
    SponsoredRow,
    SponsoredStatRow,
    flag_on,
    lineup_contains,
    upcoming_filter,
)
from partylinks.enrichment import ABROAD_AREA, OUTSIDE_AREA, meters
from partylinks.logs import setup_logging
from partylinks.media import VARIANT_WIDTHS, variant_path
from partylinks.models import EntryType
from partylinks.scheduler import RETENTION_JOB_ID, build_scheduler, next_run_times
from partylinks.scrapers import SCRAPERS, configured_sources, source_errors
from partylinks.search import by_skeleton, did_you_mean, relevance, smart_filters, sounds_like
from partylinks.security import SecurityMiddleware
from partylinks.service import ScrapeService, SourceBusyError
from partylinks.weather import router as weather_router


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

    @model_validator(mode="after")
    def _go(self) -> ChangeFeedItem:
        self.url = seo.go_path(self.event_id)  # never the seller's link itself
        return self


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
    # Our /go/ redirect, not the seller's link (see seo.go_path).
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
    has_discount: bool = False
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

    @model_validator(mode="after")
    def _go(self) -> EventSummaryOut:
        self.url = seo.go_path(self.id)
        return self


class DealOut(_Out):
    """The cheapest listing of an event across all sources (free entry wins)."""

    id: str
    source: str
    url: str
    min_price: float | None
    has_free_entry: bool
    entry_type: str

    @model_validator(mode="after")
    def _go(self) -> DealOut:
        self.url = seo.go_path(self.id)
        return self


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

    @model_validator(mode="after")
    def _go(self) -> AlternativeOut:
        self.url = seo.go_path(self.id)
        return self


class EventOut(EventSummaryOut):
    description: str | None
    source_genres: list[str]
    artists: list[str]
    organizer: str | None
    extra: dict[str, Any]

    @model_validator(mode="after")
    def _no_seller_link(self) -> EventOut:
        self.extra = {k: v for k, v in self.extra.items() if k != "purchase_url"}
        return self

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
    # A search that found nothing: the closest known name that finds something ("did you mean").
    did_you_mean: str | None = None


class RailsOut(BaseModel):
    """The home page's rows under "Trending": a pool per row (the page shows a random dozen)."""

    rails: dict[str, list[EventSummaryOut]] = {}


class SponsoredOut(BaseModel):
    """The home page's two paid rails, each in the admin's order."""

    right: list[EventSummaryOut] = []
    left: list[EventSummaryOut] = []


class SponsoredHitIn(BaseModel):
    event_id: str = Field(max_length=128)
    kind: Literal["view", "click"]


class MapEventOut(_Out):
    """A pin on the map: only what its popup shows."""

    id: str
    slug: str | None
    title: str
    start_at: datetime | None
    venue_name: str | None
    latitude: float
    longitude: float
    source: str
    sources: list[str] = []


class MapOut(BaseModel):
    """Every matching event that has coordinates, in one response."""

    items: list[MapEventOut]
    unplaced: int  # matching events without coordinates


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


class StoreOut(BaseModel):
    name: str
    events: int  # upcoming events it sells, in Israel and abroad (every listing of a group counts, once per group)
    updated_at: datetime | None  # end of its last scrape that saved events (success or partial)


class Stores(BaseModel):
    stores: list[StoreOut]


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
    countries: list[FacetValue]  # ISO codes of events abroad (the abroad page's filter)
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
    trending = "-group_size"  # listed on the most ticket sites first


_SORT_COLUMNS = {
    EventSort.start_asc: EventRow.start_at.asc(),
    EventSort.start_desc: EventRow.start_at.desc(),
    EventSort.price_asc: EventRow.group_min_price.asc(),
    EventSort.price_desc: EventRow.group_min_price.desc(),
    EventSort.seen_desc: EventRow.last_seen_at.desc(),
    EventSort.trending: EventRow.group_size.desc(),
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


class ResponseCache:
    """Bodies of public reads (JSON, rendered pages). An entry goes stale when a write commits
    (``Database.changes``, unless ``follow_writes`` is off) or ``ttl`` passes ("upcoming" and "free until" move with the clock); a
    stale entry younger than ``max_stale`` is still served at once while a background thread
    rebuilds it, so only the first visit to a page (or one after a long quiet spell) waits for a build.

    ponytail: per-process dict, cleared when it outgrows ``max_bytes``; fine for the single uvicorn
    worker. Two rebuild threads: a burst of stale pages refreshes in turn, not all at once.
    """

    def __init__(
        self, ttl: float = 60, max_bytes: int = 64 << 20, max_stale: float = 3600, follow_writes: bool = True
    ) -> None:
        self.ttl, self.max_bytes, self.max_stale, self.follow_writes = ttl, max_bytes, max_stale, follow_writes
        self._items: dict[tuple[str, str], tuple[float, int, bytes, int]] = {}  # built at, changes, body, status
        self._builders: dict[tuple[str, str], Callable[[], bytes | tuple[bytes, int]]] = {}  # to refresh a peeked entry
        self._bytes = 0
        self._lock = threading.Lock()
        self._refreshing: set[tuple[str, str]] = set()
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cache-refresh")
        # A crawler opening dozens of cold pages at once builds them a few at a time, so the GIL is
        # left for the event loop serving cached pages (a link preview gives up after a few seconds).
        self._builds = threading.BoundedSemaphore(4)

    def peek(
        self, key: tuple[str, str], build: Callable[[], bytes | tuple[bytes, int]] | None = None
    ) -> tuple[bytes, int] | None:
        """(body, status) of ``key`` if cached, fresh or stale (a stale one is rebuilt in the
        background); None when it has to be built first. Never builds in the caller's thread."""
        if hit := self._items.get(key):
            built_at, changes, body, code = hit
            age = time.monotonic() - built_at
            if (changes == Database.changes or not self.follow_writes) and age < self.ttl:
                return body, code
            build = build or self._builders.get(key)
            if age < self.max_stale and build:
                self._refresh(key, build)
                return body, code
        return None

    def get(self, key: tuple[str, str], build: Callable[[], bytes | tuple[bytes, int]]) -> tuple[bytes, int]:
        """(body, status) of ``key``; ``build`` returns the body, or (body, status)."""
        return self.peek(key, build) or self._build(key, build)

    def _build(self, key: tuple[str, str], build: Callable[[], bytes | tuple[bytes, int]]) -> tuple[bytes, int]:
        with self._builds:
            changes, now = Database.changes, time.monotonic()  # read before building: a write during it wins
            out = build()
        body, code = out if isinstance(out, tuple) else (out, 200)
        with self._lock:
            if old := self._items.pop(key, None):
                self._bytes -= len(old[2])
            if self._bytes + len(body) > self.max_bytes:
                self._items.clear()
                self._builders.clear()
                self._bytes = 0
            self._items[key] = (now, changes, body, code)
            self._builders[key] = build
            self._bytes += len(body)
        return body, code

    def _refresh(self, key: tuple[str, str], build: Callable[[], bytes | tuple[bytes, int]]) -> None:
        with self._lock:
            if key in self._refreshing:
                return
            self._refreshing.add(key)

        def run() -> None:
            try:
                self._build(key, build)
            except Exception:
                log.exception("cache refresh of %s failed", key[0])
            finally:
                with self._lock:
                    self._refreshing.discard(key)

        self._pool.submit(run)


class CachedPages:
    """Serves a cached page or listing straight from the event loop, before routing. The routes are
    sync and run on the threadpool: when a crawler fills it with cold pages, even a cached home page
    waited there, and WhatsApp's preview of spotx.fun timed out with no title or image."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope["method"] == "GET":
            state = scope["app"].state
            key = (scope.get("root_path", "") + scope["path"], scope.get("query_string", b"").decode("latin-1"))
            for cache, media in ((getattr(state, "html_cache", None), b"text/html; charset=utf-8"),
                                 (state.response_cache, b"application/json")):  # fmt: skip
                if cache and (hit := cache.peek(key)):
                    body, code = hit
                    await send({"type": "http.response.start", "status": code, "headers": [
                        (b"content-type", media), (b"content-length", str(len(body)).encode()),
                    ]})  # fmt: skip
                    return await send({"type": "http.response.body", "body": body})
        await self.app(scope, receive, send)


def _cached(request: Request, build: Callable[[Session], BaseModel], *, exclude_none: bool = False) -> Response:
    """The endpoint's JSON from the response cache. ``build`` gets its own session: it may run
    after the request, refreshing a stale entry in the background."""
    cache: ResponseCache = request.app.state.response_cache
    db: Database = request.app.state.db

    def body() -> bytes:
        with db.session(write=False) as session:
            return build(session).model_dump_json(by_alias=True, exclude_none=exclude_none).encode()

    return Response(cache.get((request.url.path, request.url.query), body)[0], media_type="application/json")


SOUND_INDEX_TTL_S = 600


def _sound_index(request: Request) -> dict[str, list[str]]:
    """Every lineup name by its skeleton, rebuilt at most every 10 minutes."""
    state = request.app.state
    built, index = getattr(state, "sound_index", (0.0, {}))
    if time.monotonic() - built > SOUND_INDEX_TTL_S:
        db: Database = state.db
        with db.session(write=False) as session:
            lineups = session.scalars(select(EventRow.lineup).where(EventRow.lineup.is_not(None))).all()
        index = by_skeleton(str(n) for lineup in lineups for n in lineup or [])
        state.sound_index = (time.monotonic(), index)
    return index


def _sounds_like(request: Request, q: str) -> list[str]:
    """Lineup names that sound like ``q`` in another spelling: "אינפקטד מאשרום" finds Infected Mushroom."""
    return sounds_like(q, _sound_index(request))


# Built in the background on startup (an empty cache made the first visits after a deploy take 3-12 s).
WARM_PATHS = ("/", "/artists", "/venues", "/stores", "/tonight", "/weekend", "/map", "/abroad", "/blog", "/posts", "/categories", "/categories?anywhere=true", "/ticket-sites", "/events/rails", "/sitemap.xml")

# The home page's rows (/events/rails), in order: event filters, plus "days" ahead (default 30) and
# "multi" (only events on more than one ticket site) and "exclude" (none of these genres).
# One cached response for all of them.
HOME_RAILS: dict[str, dict[str, Any]] = {
    "trending": {"days": 14, "multi": True},
    # A night out: evening and night, music, parties and shows only, nothing for kids.
    "tonight": {
        "today": True,
        "time_slot": ["evening", "night"],
        "genre": [
            "techno", "house", "trance", "hip_hop", "rnb", "mainstream", "pop", "reggaeton_latin", "afro",
            "dancehall_reggae", "mizrahi", "israeli", "greek", "arabic", "disco_funk", "drum_and_bass", "bass_dubstep",
            "hardstyle", "edm", "electronic", "throwback", "rock_indie", "jazz", "world_balkan", "dark_wave",
            "live_show", "lgbt", "stand_up", "karaoke", "festival", "happy_hour", "theatre", "classical_opera",
        ],
        "exclude": ["kids", "lectures", "workshops", "conference"],
    },
    "electronic": {
        "genre": ["techno", "house", "trance", "electronic", "edm", "drum_and_bass", "bass_dubstep", "hardstyle"]
    },
    "free": {"free_entry": True},
    "live": {"genre": ["live_show"]},
    "israeli": {"genre": ["israeli", "mizrahi", "greek"]},
    "mainstream": {"genre": ["mainstream", "hip_hop", "rnb", "reggaeton_latin", "throwback", "pop"]},
    "standup": {"genre": ["stand_up"]},
    "festival": {"genre": ["festival"]},
    "theatre": {"genre": ["theatre", "dance"]},
    "jazz": {"genre": ["jazz", "classical_opera"]},
    "kids": {"genre": ["kids"]},
    "lectures": {"genre": ["lectures", "workshops", "culture", "exhibitions"]},
    "hitech": {"genre": ["hi_tech", "ai", "cyber", "software_dev", "startups", "conference"]},
}
RAIL_POOL = 30


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
        async def maintenance() -> None:
            """Before any source writes: artwork still in the database moves to files, and a file left
            mostly empty is compacted (a big file is a slow one: every query reads across it). Then
            genres are re-read if their config changed, the scrapers start, and map coordinates are
            filled for address-only events."""
            jobs: list[Callable[[], object]] = [service.media.move_to_files, db.compact_if_bloated, service.recompute_genres]
            for job in jobs:
                try:
                    await asyncio.to_thread(job)
                except Exception:  # the site and the scrapers run regardless
                    log.exception("startup maintenance step %s failed", getattr(job, "__name__", job))
            # WebP copies older files still lack: in the background (an hour or so after the first
            # deploy), so the scrapers start now; new downloads make their own copies.
            copies = asyncio.create_task(asyncio.to_thread(service.media.build_missing_variants))
            app.state.tasks.add(copies)
            copies.add_done_callback(app.state.tasks.discard)
            if scheduler:
                scheduler.start()
            await asyncio.to_thread(service.geocode)

        async def warm() -> None:
            """The busiest pages built once right after a deploy, so the first visitors don't wait for them."""
            await asyncio.sleep(1)  # let startup finish first
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
                for path in WARM_PATHS:
                    try:
                        await client.get(path)
                    except Exception:  # a cold page is only slower
                        log.exception("cache warm-up of %s failed", path)

        for job in (maintenance(), *([warm()] if run_scheduler else [])):  # the server that serves visitors
            task = asyncio.create_task(job)
            app.state.tasks.add(task)
            task.add_done_callback(app.state.tasks.discard)
        try:
            server_status.log_startup(settings, db, scheduler)
        except Exception:  # the banner never blocks startup
            log.exception("status: startup report failed")
        if settings.logging.status_every_minutes:
            beat = asyncio.create_task(server_status.status_loop(settings, db, scheduler))
            app.state.tasks.add(beat)
            beat.add_done_callback(app.state.tasks.discard)
        try:
            yield
        finally:
            server_status.log_shutdown(settings)
            if scheduler and scheduler.running:
                scheduler.shutdown(wait=False)
            for task in app.state.tasks:
                task.cancel()
            service.shutdown()
            db.engine.dispose()

    docs = settings.security.api_docs
    app = FastAPI(
        title="SPOTX API",
        version="0.1.0",
        description="Party events across Israel scraped from Israeli ticketing sites.",
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.state.settings = settings  # read by SecurityMiddleware before the lifespan finishes too
    app.state.response_cache = ResponseCache()
    app.add_middleware(CachedPages)  # inside gzip and security: a cached page is still compressed and guarded
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    # CORS: only the site itself may read the API from a browser (the app is same-origin anyway);
    # other sites' pages get no Access-Control-Allow-Origin, so their reads are blocked.
    app.add_middleware(CORSMiddleware, allow_origins=[settings.site.base_url.rstrip("/")], allow_methods=["GET"])
    app.add_middleware(SecurityMiddleware)  # outermost: rejects before any work is done
    _register_routes(app)
    app.include_router(admin_router)
    app.include_router(weather_router)
    # The React site (web/), once built with `npm run build`. Mounted last so API routes win.
    site = Path(__file__).resolve().parents[2] / "web" / "dist"
    seo.register(app, site)  # server-rendered pages + sitemap/robots/llms.txt, before the static catch-all
    if site.is_dir():
        app.mount("/", _SiteFiles(directory=site, html=True), name="site")
    return app


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.db.session(write=False) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def _best_deals(session: Session, rows: Sequence[EventRow]) -> dict[str, DealOut]:
    """Cheapest listing per event, loaded in one query for a whole page."""
    ids = {r.best_listing_id for r in rows if r.best_listing_id}
    if not ids:
        return {}
    deal_columns = load_only(
        EventRow.id, EventRow.source, EventRow.url, EventRow.min_price, EventRow.has_free_entry, EventRow.entry_type
    )
    return {
        d.id: DealOut.model_validate(d)
        for d in session.scalars(select(EventRow).where(EventRow.id.in_(ids)).options(deal_columns))
    }


def _group_sources(session: Session, rows: Sequence[EventRow]) -> dict[str, list[str]]:
    """Ticket sites per duplicate group, loaded in one query for a whole page."""
    groups = {r.group_id for r in rows if r.group_id}
    found: dict[str, set[str]] = {}
    if groups:
        for group_id, source in session.execute(
            select(EventRow.group_id, EventRow.source).where(EventRow.group_id.in_(groups)).distinct()
        ):
            if group_id:
                found.setdefault(group_id, set()).add(source)
    return {r.id: [r.source, *sorted(found.get(r.group_id or "", set()) - {r.source})] for r in rows}


def _group_covers(session: Session, rows: Sequence[EventRow]) -> dict[str, str]:
    """Artwork borrowed from a duplicate listing for events whose own source has none."""
    groups = {r.group_id for r in rows if r.group_id and not r.cover_url and not r.image_url}
    found: dict[str, str] = {}
    if groups:
        for group_id, image_id in session.execute(
            select(EventRow.group_id, ImageRow.id)
            .join(ImageRow.event)
            .where(EventRow.group_id.in_(groups), ImageRow.downloaded_at.is_not(None))
            .order_by(ImageRow.position, ImageRow.id)
        ):
            if group_id:
                found.setdefault(group_id, f"/images/{image_id}")
    return found


def _summary_extras(session: Session, rows: Sequence[EventRow]) -> dict[str, dict[str, Any]]:
    deals = _best_deals(session, rows)
    sources = _group_sources(session, rows)
    covers = _group_covers(session, rows)
    return {
        r.id: {
            "best_deal": deals.get(r.best_listing_id) if r.best_listing_id else None,
            "sources": sources[r.id],
            "cover_url": r.cover_url or covers.get(r.group_id or ""),
        }
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


def hide_email(address: str) -> str:
    """'a@b.c' -> base64 of 'c.b@a': no address pattern for a harvester to match."""
    return base64.b64encode(address[::-1].encode()).decode() if address else ""


def _json_contains(column: Any, values: list[str]) -> ColumnElement[bool]:
    """Any-of match on a JSON string array (portable: SQLite and PostgreSQL)."""
    return or_(*(cast(column, String).ilike(f'%"{v}"%') for v in values))


def _register_routes(app: FastAPI) -> None:
    @app.get("/site", tags=["meta"])
    def site_info(request: Request) -> dict[str, Any]:
        """Public site details the website shows (name, accessibility and privacy contacts)."""
        site = request.app.state.settings.site
        # Addresses never travel as text (harvesters read JSON too): reversed and base64-encoded, decoded
        # in the browser only when a person asks to see one (web/src/ui.tsx HiddenEmail).
        return {
            "name": site.name,
            "base_url": site.base_url,
            "email": hide_email(site.contact_email),
            "accessibility": site.accessibility.model_dump(mode="json") | {"email": hide_email(site.accessibility.email)},
            "privacy": site.privacy.model_dump(mode="json") | {"email": hide_email(site.privacy.email)},
        }

    @app.get("/health", tags=["meta"])
    def health(request: Request, response: Response) -> dict[str, Any]:
        """Liveness + readiness: DB reachable, scheduler alive, per-source freshness, config errors."""
        state = request.app.state
        cfg: Settings = state.settings
        report: dict[str, Any] = {"status": "ok", "checks": {}}
        try:
            with state.db.session(write=False) as session:
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
        report["checks"]["scheduler"] = (
            "disabled" if scheduler is None else ("running" if scheduler.running else "stopped")
        )
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
        if (
            (scheduler is not None and not scheduler.running)
            or report["checks"]["config_errors"]
            or retention.last_error
        ):
            report["status"] = "degraded"
        try:  # the details (source names, error messages) are for the admin; monitors only need the status
            require_admin(request)
        except HTTPException:
            return {"status": report["status"]}
        return report

    def event_filters(
        request: Request,
        source: Annotated[
            list[str] | None, Query(max_length=MAX_LIST_PARAMS, description="Source name (repeatable)")
        ] = None,
        genre: Annotated[
            list[str] | None, Query(max_length=MAX_LIST_PARAMS, description="Canonical genre, any-of (repeatable)")
        ] = None,
        artist: Annotated[
            str | None, Query(min_length=2, max_length=MAX_TEXT_PARAM, description="DJ / performer in the lineup")
        ] = None,
        venue: Annotated[
            str | None, Query(min_length=2, max_length=MAX_TEXT_PARAM, description="Venue name contains")
        ] = None,
        venue_slug: Annotated[str | None, Query(description="Exact venue slug")] = None,
        city: Annotated[str | None, Query(min_length=2, max_length=MAX_TEXT_PARAM, description="City contains")] = None,
        in_target_city: Annotated[bool | None, Query(description="Matched to the configured target city")] = None,
        abroad: Annotated[
            bool | None, Query(description="Events abroad instead of events in Israel (default: Israel, both for an artist or venue)")
        ] = None,
        country: Annotated[str | None, Query(min_length=2, max_length=2, description="Country code (ISO 3166 alpha-2), events abroad")] = None,
        anywhere: Annotated[bool, Query(description="Events in Israel and abroad")] = False,
        entry_type: Annotated[
            list[EntryType] | None, Query(max_length=MAX_LIST_PARAMS, description="free / paid / free_and_paid")
        ] = None,
        free_entry: Annotated[bool | None, Query(description="Has at least one free ticket tier")] = None,
        sold_out: bool | None = None,
        discount: Annotated[
            bool | None, Query(description="A listing of the event is discounted or offers a code")
        ] = None,
        day_of_week: Annotated[
            list[str] | None, Query(max_length=MAX_LIST_PARAMS, description="e.g. thursday (repeatable)")
        ] = None,
        time_slot: Annotated[
            list[str] | None, Query(max_length=MAX_LIST_PARAMS, description="e.g. night (repeatable)")
        ] = None,
        area: Annotated[
            list[str] | None, Query(max_length=MAX_LIST_PARAMS, description="Area of the city, e.g. south (repeatable)")
        ] = None,
        region: Annotated[
            list[str] | None,
            Query(max_length=MAX_LIST_PARAMS, description="Region of the country, any-of: tel_aviv or a region key"),
        ] = None,
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
        q: Annotated[
            str | None,
            Query(min_length=2, max_length=MAX_TEXT_PARAM, description="Search title/description/venue/lineup"),
        ] = None,
    ) -> list[ColumnElement[bool]]:
        """WHERE clauses of the event filters, shared by the list and the map."""
        cfg: Settings = request.app.state.settings
        tz = ZoneInfo(cfg.timezone)
        filters: list[ColumnElement[bool]] = []
        if source:
            # Any listing of the event's group sold there: a party also on Zygo shows under Zygo
            # even when the group's primary listing is Go-Out's.
            listing = aliased(EventRow)
            filters.append(EventRow.group_id.in_(select(listing.group_id).where(listing.source.in_(source))))
        if genre:
            filters.append(_json_contains(EventRow.genres, cfg.enrichment.expand_genres(genre)))
        if artist:
            filters.append(lineup_contains(artist))
        if venue:
            filters.append(EventRow.venue_name.ilike(_contains(venue), escape="\\"))
        if venue_slug:
            filters.append(EventRow.venue_slug == venue_slug)
        if city:
            filters.append(
                or_(
                    EventRow.city.ilike(_contains(city), escape="\\"),
                    EventRow.address.ilike(_contains(city), escape="\\"),
                )
            )
        if in_target_city is not None:
            filters.append(EventRow.in_target_city.is_(in_target_city))
        # Search, an artist's or a venue's page and the artist / venue indexes cover every event, abroad too.
        if abroad is not None or not (q or artist or venue or venue_slug or anywhere):
            filters.append(EventRow.country.is_not(None) if abroad else EventRow.country.is_(None))
        if country:
            filters.append(EventRow.country == country.upper())
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
        if discount is not None:
            listing = aliased(EventRow)
            discounted = EventRow.group_id.in_(select(listing.group_id).where(listing.has_discount.is_(True)))
            filters.append(discounted if discount else not_(discounted))
        if day_of_week:
            filters.append(EventRow.day_of_week.in_([d.lower() for d in day_of_week]))
        if time_slot:
            filters.append(EventRow.time_slot.in_(time_slot))
        if area:
            filters.append(EventRow.area.in_(area))
        if region:
            # Tel Aviv is the target city (its events carry neighbourhood areas); other regions are area keys.
            any_of = [EventRow.area.in_([r for r in region if r != "tel_aviv"])]
            if "tel_aviv" in region:
                any_of.append(EventRow.in_target_city.is_(True))
            filters.append(or_(*any_of))
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
            filters += smart_filters(q, cfg, _sounds_like(request, q))
        return filters

    # A default, not Annotated: string annotations (``from __future__``) cannot see this local function.
    event_filters_dep = Depends(event_filters)

    @app.get("/events", response_model=EventPage, tags=["events"])
    def list_events(
        request: Request,
        filters: list[ColumnElement[bool]] = event_filters_dep,
        sort: EventSort = EventSort.start_asc,
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int | None, Query(ge=1)] = None,
    ) -> Response:
        cfg: Settings = request.app.state.settings
        size = min(page_size or cfg.api.default_page_size, cfg.api.max_page_size)
        if page > cfg.api.max_page:  # no paging through every event: narrow the filters instead
            raise HTTPException(400, f"page is at most {cfg.api.max_page}")

        # A search in the default date order puts title matches first, then lineup / venue, then the rest.
        q = request.query_params.get("q")
        rank = relevance(q, cfg, _sounds_like(request, q)) if q and sort is EventSort.start_asc else None

        def build(session: Session) -> EventPage:
            # Cards never show the long text columns.
            card = select(EventRow).options(selectinload(EventRow.images), defer(EventRow.description), defer(EventRow.extra))
            if rank is None:
                total = session.scalar(select(func.count()).select_from(EventRow).where(*filters)) or 0
                rows = session.scalars(
                    card.where(*filters)
                    .order_by(_SORT_COLUMNS[sort], EventRow.start_at, EventRow.id)
                    .offset((page - 1) * size)
                    .limit(size)
                ).all()
            else:
                # Ranking reads every match anyway, so one pass gives both the order and the total
                # (measured at 35k events: as fast as the count + page it replaces).
                ids = session.scalars(select(EventRow.id).where(*filters).order_by(rank, EventRow.start_at, EventRow.id)).all()
                total, wanted = len(ids), ids[(page - 1) * size : page * size]
                found = {r.id: r for r in session.scalars(card.where(EventRow.id.in_(wanted)))}
                rows = [found[i] for i in wanted if i in found]
            suggestion = None
            if q and not total:  # what people look for and we lack; once per cached answer
                log.info("search miss: %r", q[:100])
                names = (n for names in _sound_index(request).values() for n in names)
                if (near := did_you_mean(q, cfg, names)) and session.scalar(
                    # the search is the last filter: the other filters stay
                    select(EventRow.id).where(*filters[:-1], *smart_filters(near, cfg)).limit(1)
                ):
                    suggestion = near
            extras = _summary_extras(session, rows)
            items = [EventSummaryOut.model_validate(r).model_copy(update=extras[r.id]) for r in rows]
            return EventPage(total=total, page=page, page_size=size, items=items, did_you_mean=suggestion)

        return _cached(request, build)

    @app.get("/events/map", response_model=MapOut, tags=["events"])
    def map_events(request: Request, filters: list[ColumnElement[bool]] = event_filters_dep) -> Response:
        """All matching events with coordinates at once (the list's filters, no paging)."""

        def build(session: Session) -> MapOut:
            located = and_(EventRow.latitude.is_not(None), EventRow.longitude.is_not(None))
            rows = session.execute(
                select(
                    EventRow.id, EventRow.slug, EventRow.title, EventRow.start_at, EventRow.venue_name,
                    EventRow.latitude, EventRow.longitude, EventRow.source, EventRow.group_id,
                )
                .where(*filters, located)
                .order_by(EventRow.start_at, EventRow.id)
            ).all()  # fmt: skip
            unplaced = session.scalar(select(func.count()).select_from(EventRow).where(*filters, not_(located))) or 0
            sources = _group_sources(session, rows)  # type: ignore[arg-type]
            items = [MapEventOut.model_validate(r).model_copy(update={"sources": sources[r.id]}) for r in rows]
            return MapOut(items=items, unplaced=unplaced)

        return _cached(request, build)

    @app.get("/go/{event_id:path}", include_in_schema=False)
    def go(event_id: str, request: Request, session: SessionDep, at: str | None = None) -> RedirectResponse:
        """The seller's page of one listing: the only place a ticket link leaves the server
        (one click at a time, rate-limited by SecurityMiddleware), counted, and UTM-tagged for the
        seller's analytics. ``at`` = where on our page it was clicked (utm.PLACEMENTS)."""
        row = session.get(EventRow, event_id)
        if not row or not row.url.startswith(("https://", "http://")):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found")
        headers = {"Cache-Control": "private, no-store", "X-Robots-Tag": "noindex, nofollow"}
        clicks.record(session, row, at)
        url = utm.tagged(row.url, request.app.state.settings.utm, row, at)
        return RedirectResponse(url, status.HTTP_302_FOUND, headers=headers)

    # Before /events/{event_ref}, which would otherwise take "sponsored" for a slug.
    @app.get("/events/sponsored", response_model=SponsoredOut, tags=["events"])
    def sponsored(session: SessionDep) -> SponsoredOut:
        """Paid placements (picked in the admin) that are still upcoming and within their campaign;
        none at all while the admin's sponsored switch is off. Not in the response cache: an admin
        change shows on the next request (the browser keeps its own copy)."""
        if not flag_on(session, "sponsored"):
            return SponsoredOut()
        pairs = session.execute(
            select(SponsoredRow.side, EventRow)
            .join(EventRow, EventRow.id == SponsoredRow.event_id)
            .where(upcoming_filter(), or_(SponsoredRow.until.is_(None), SponsoredRow.until > datetime.now(UTC)))
            .order_by(SponsoredRow.position)
            .options(selectinload(EventRow.images), defer(EventRow.description), defer(EventRow.extra))
        ).all()
        extras = _summary_extras(session, [e for _, e in pairs])
        out = SponsoredOut()
        for side, e in pairs:
            getattr(out, side).append(EventSummaryOut.model_validate(e).model_copy(update=extras[e.id]))
        return out

    @app.get("/events/rails", response_model=RailsOut, tags=["events"])
    def rails(request: Request) -> Response:
        """Every home-page row (HOME_RAILS) in one cached response, warmed on startup: the busiest
        events of each, from tonight to the row's horizon."""
        cfg: Settings = request.app.state.settings

        def build(session: Session) -> RailsOut:
            now = datetime.now(ZoneInfo(cfg.timezone))
            out = RailsOut()
            for key, spec in HOME_RAILS.items():
                kw = {k: v for k, v in spec.items() if k not in ("days", "multi", "today", "exclude")}
                if spec.get("today"):
                    kw["on_date"] = now.date()
                filters = event_filters(request, date_to=now + timedelta(days=spec.get("days", 30)), **kw)
                if spec.get("multi"):
                    filters.append(EventRow.group_size > 1)
                if spec.get("exclude"):
                    filters.append(not_(_json_contains(EventRow.genres, cfg.enrichment.expand_genres(spec["exclude"]))))
                rows = session.scalars(
                    select(EventRow)
                    .where(*filters)
                    .order_by(_SORT_COLUMNS[EventSort.trending], EventRow.start_at, EventRow.id)
                    .limit(RAIL_POOL)
                    .options(selectinload(EventRow.images), defer(EventRow.description), defer(EventRow.extra))
                ).all()
                extras = _summary_extras(session, rows)
                out.rails[key] = [EventSummaryOut.model_validate(r).model_copy(update=extras[r.id]) for r in rows]
            return out

        return _cached(request, build)

    @app.post("/events/sponsored/hit", status_code=status.HTTP_204_NO_CONTENT, include_in_schema=False)
    def sponsored_hit(body: SponsoredHitIn, request: Request, session: SessionDep) -> Response:
        """One view or click of a sponsored placement (sent by the rails). Only placements on show count."""
        event = session.get(EventRow, body.event_id)
        if event and session.get(SponsoredRow, body.event_id) and flag_on(session, "sponsored"):
            day = datetime.now(ZoneInfo(request.app.state.settings.timezone)).date()
            # ponytail: read-then-write, so two first hits of a day at the same instant can clash (one is lost).
            stat = session.get(SponsoredStatRow, (body.event_id, day))
            if stat is None:
                stat = SponsoredStatRow(event_id=body.event_id, day=day, title=event.title, views=0, clicks=0)
                session.add(stat)
            if body.kind == "view":
                stat.views += 1
            else:
                stat.clicks += 1
        return Response(status_code=status.HTTP_204_NO_CONTENT)

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
        kind: Annotated[
            list[str] | None, Query(max_length=MAX_LIST_PARAMS, description="e.g. price_down (repeatable)")
        ] = None,
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
        return _image_response(image_id, request, session)

    @app.get("/images/{image_id}/{width}.webp", tags=["events"], response_class=Response)
    def get_image_copy(image_id: int, width: int, request: Request, session: SessionDep) -> Response:
        """The artwork resized to ``width`` (640 cards, 1280 event page) as WebP. A copy not made
        yet (or never: animations, unreadable files) serves the original, cached for an hour only,
        so the edge picks up the copy once it exists. Never converts on request."""
        if width not in VARIANT_WIDTHS:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such size")
        return _image_response(image_id, request, session, width)

    def _image_response(image_id: int, request: Request, session: Session, width: int | None = None) -> Response:
        meta = session.execute(
            select(ImageRow.downloaded_at, ImageRow.content_type, ImageRow.local_path).where(ImageRow.id == image_id)
        ).first()
        if meta is None or meta.downloaded_at is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "image not stored")
        etag = f'"{image_id}-{int(meta.downloaded_at.timestamp())}"'
        headers = {"Cache-Control": "public, max-age=86400", "ETag": etag}
        if width is not None:
            copy = request.app.state.service.media.path_for(variant_path(meta.local_path, width)) if meta.local_path else None
            if copy is not None and copy.is_file():
                # An id's picture never changes (a new URL is a new row), so its copy is immutable.
                headers = {"Cache-Control": "public, max-age=31536000, immutable", "ETag": f'"{image_id}-{width}"'}
                if request.headers.get("if-none-match") == headers["ETag"]:
                    return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
                return FileResponse(copy, media_type="image/webp", headers=headers)
            headers["Cache-Control"] = "public, max-age=3600"  # the original, until the copy exists
        if request.headers.get("if-none-match") == etag:  # client already has it: no blob read
            return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
        image = session.scalar(select(ImageRow).where(ImageRow.id == image_id).options(undefer(ImageRow.data)))
        if image is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "image not stored")
        media_type = meta.content_type or "application/octet-stream"
        if image.data is not None:
            return Response(content=image.data, media_type=media_type, headers=headers)
        media = request.app.state.service.media
        path = media.path_for(image.local_path or "")
        if image.local_path and not path.is_file():  # converted: the original is gone, its 1280 copy stands in
            path, media_type = media.path_for(variant_path(image.local_path, VARIANT_WIDTHS[-1])), "image/webp"
        if not image.local_path or not path.is_file():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "image file missing")
        return FileResponse(path, media_type=media_type, headers=headers)

    @app.get("/categories", response_model=Categories, response_model_exclude_none=True, tags=["events"])
    def categories(
        request: Request,
        filters: list[ColumnElement[bool]] = event_filters_dep,
        upcoming: bool = True,
    ) -> Response:
        """Facet counts of the events matching the list's filters (none: every upcoming event)."""
        return _cached(request, lambda session: build_categories(request, session, filters, upcoming), exclude_none=True)

    @app.get("/ticket-sites", response_model=Stores, tags=["events"])
    def stores(request: Request) -> Response:
        """The ticket sites events come from, with how many upcoming events each sells (the stores page)."""

        def build(session: Session) -> Stores:
            listings = select(EventRow.group_id, EventRow.source).where(upcoming_filter()).distinct()
            counts = Counter(source for _, source in session.execute(listings))
            last_runs = (
                select(ScrapeRunRow.source, func.max(ScrapeRunRow.finished_at))
                .where(ScrapeRunRow.status.in_(("success", "partial")))
                .group_by(ScrapeRunRow.source)
            )
            done = {source: finished for source, finished in session.execute(last_runs)}
            # Every enabled source shows, even with no upcoming events saved yet (a new one, a local copy).
            for name, cfg in configured_sources(request.app.state.settings).items():
                if cfg.enabled:
                    counts.setdefault(name, 0)
            return Stores(stores=[StoreOut(name=s, events=n, updated_at=done.get(s)) for s, n in counts.most_common()])

        return _cached(request, build)

    @app.get("/locate", tags=["events"])
    def locate(
        request: Request,
        lat: Annotated[float, Query(ge=-90, le=90)],
        lng: Annotated[float, Query(ge=-180, le=180)],
    ) -> dict[str, str | None]:
        """Region (and canonical city, when inside one) of a point: "near me" on the home page."""
        cfg: Settings = request.app.state.settings
        here = (lat, lng)
        city = min(
            (c for c in cfg.enrichment.cities if c.center and meters(here, c.center) <= c.radius_m),
            key=lambda c: meters(here, c.center),  # type: ignore[arg-type]
            default=None,
        )
        if city and city.key == "tel-aviv":
            return {"region": "tel_aviv", "city": None}
        region = request.app.state.service.enricher.areas.region(None, None, None, lat, lng)
        known = region not in (ABROAD_AREA, OUTSIDE_AREA)
        return {"region": region if known else None, "city": city.he if city else None}

    def build_categories(
        request: Request, session: Session, filters: list[ColumnElement[bool]], upcoming: bool
    ) -> Categories:
        # Only the facet columns (not descriptions/extra JSON); each real event counted once.
        query = select(
            EventRow.genres, EventRow.local_date, EventRow.day_of_week, EventRow.time_slot, EventRow.area,
            EventRow.group_entry_type, EventRow.venue_name, EventRow.city, EventRow.lineup, EventRow.event_types,
            EventRow.source, EventRow.in_target_city, EventRow.country,
        ).where(*filters)  # fmt: skip
        rows = session.execute(query).all()

        def facet(values: Callable[[Any], list[Any]]) -> list[FacetValue]:
            counts = Counter(str(v) for row in rows for v in values(row) if v not in (None, ""))
            return [FacetValue(value=v, events=n) for v, n in counts.most_common()]

        def artist_facet() -> list[FacetValue]:
            # "Pettra" / "PETTRA" are one artist: merge case-insensitively, show the most used spelling.
            spellings = Counter(str(v) for row in rows for v in row.lineup or [] if v)
            merged: dict[str, tuple[str, int]] = {}
            for name, n in spellings.most_common():
                shown, total = merged.get(name.casefold(), (name, 0))
                merged[name.casefold()] = (shown, total + n)
            ranked = sorted(merged.values(), key=lambda item: -item[1])  # all of them: the site searches the list
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

        # Per ticket site: real events it sells (every listing of a group counts, once per group).
        listings = select(EventRow.group_id, EventRow.source).where(EventRow.country.is_(None)).distinct()
        if upcoming:
            listings = listings.where(upcoming_filter())
        group_sources = Counter(source for _, source in session.execute(listings))

        region_keys = request.app.state.service.enricher.areas.region_keys

        def region(r: Any) -> str | None:
            return "tel_aviv" if r.in_target_city else r.area if r.area in region_keys else None

        # The same regions as the home filter (not Tel Aviv neighbourhoods mixed with regions).
        venue_area = {r.venue_name: reg for r in rows if r.venue_name and (reg := region(r))}

        city_regions = Counter((r.city, reg) for r in rows if r.city and (reg := region(r)))
        city_region: dict[str, str] = {}
        for (city, reg), _ in city_regions.most_common():
            city_region.setdefault(city, reg)  # a city's most common region
        return Categories(
            genres=facet(lambda r: request.app.state.settings.enrichment.with_parents(r.genres or [])),
            dates=sorted(facet(lambda r: [r.local_date]), key=lambda f: f.value),
            days_of_week=facet(lambda r: [r.day_of_week]),
            time_slots=facet(lambda r: [r.time_slot]),
            areas=facet(lambda r: [r.area]),
            regions=facet(lambda r: [region(r)]),
            entry_types=facet(lambda r: [r.group_entry_type]),
            venues=[f.model_copy(update={"area": venue_area.get(f.value)}) for f in facet(lambda r: [r.venue_name])],
            cities=[f.model_copy(update={"area": city_region.get(f.value)}) for f in facet(lambda r: [r.city])],
            countries=facet(lambda r: [r.country]),
            lineup=artist_facet(),
            event_types=facet(lambda r: r.event_types or []),
            sources=[FacetValue(value=s, events=n) for s, n in group_sources.most_common()],
        )

    @app.get("/sources", response_model=list[SourceOut], tags=["sources"], dependencies=[AdminDep])
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
            except Exception:
                log.exception("API-triggered run of %s failed", name)

        task = asyncio.create_task(run())
        request.app.state.tasks.add(task)
        task.add_done_callback(request.app.state.tasks.discard)
        return {"status": "started", "source": name}

    @app.get("/runs", response_model=list[RunOut], tags=["sources"], dependencies=[AdminDep])
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

    @app.get("/maintenance/retention", tags=["maintenance"], dependencies=[AdminDep])
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
