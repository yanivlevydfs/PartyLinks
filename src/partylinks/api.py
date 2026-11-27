"""REST API (FastAPI) + scheduler lifecycle."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import gzip
import json
import logging
import os
import re
import threading
import time
from collections import Counter
from collections.abc import AsyncGenerator, Callable, Iterable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Any
from zoneinfo import ZoneInfo

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator
from sqlalchemy import ColumnElement, Interval, String, and_, cast, func, literal, not_, null, or_, select
from sqlalchemy.exc import DataError
from sqlalchemy.orm import Session, aliased, defer, load_only, selectinload, undefer
from starlette.exceptions import HTTPException as StarletteHTTPException

from partylinks import clicks, placecards, searches, seo, shortlinks, utm
from partylinks import status as server_status
from partylinks.accounts import install as install_accounts
from partylinks.accounts import sign_in_state
from partylinks.admin import AdminDep, require_admin
from partylinks.admin import router as admin_router
from partylinks.ads import service as ads
from partylinks.artist_links.models import links_for as artist_links_for
from partylinks.artistdb import lineup_images
from partylinks.artwork import service as artwork
from partylinks.artwork.routes import router as artwork_router
from partylinks.bot import install as install_bot
from partylinks.bot.core import understand
from partylinks.bot.core.find import free_text
from partylinks.bot.telegram.webhook import username as telegram_username
from partylinks.box_offices import BoxOfficeOut, box_offices
from partylinks.config import Settings, get_settings
from partylinks.config_store import store as config_overrides
from partylinks.db import (
    Database,
    DealChangeRow,
    EventRow,
    ImageRow,
    ScrapeRunRow,
    in_person,
    lineup_has,
    open_now_filters,
    upcoming_filter,
)
from partylinks.dedup import primary_listing
from partylinks.enrichment import ABROAD_AREA, OUTSIDE_AREA, meters
from partylinks.event_text import DescriptionLayout, description_layout
from partylinks.event_text.group import group_description
from partylinks.holiday_pages import core as holiday_pages
from partylinks.holiday_pages.routes import HolidayOut
from partylinks.home_pins import rails as home_pins
from partylinks.home_rails import RowCache
from partylinks.home_rails import log_once as broken_home_rail
from partylinks.home_rails.config import HomeRail
from partylinks.lines.weekly import weekly as lines_weekly
from partylinks.logs import setup_logging
from partylinks.mail.images import router as mail_images_router
from partylinks.media import LEGACY_WIDTHS, VARIANT_WIDTHS
from partylinks.models import EntryType
from partylinks.parsing import TidyText, is_site_logo
from partylinks.pictures import live_image_url, live_images
from partylinks.places import city_abroad, city_name
from partylinks.popularity import models as popularity
from partylinks.railcards import nearest_show
from partylinks.saved.routes import router as saved_router
from partylinks.scheduler import RETENTION_JOB_ID, build_scheduler, next_run_times
from partylinks.scrapers import SCRAPERS, configured_sources, source_errors
from partylinks.search import (
    NameIndex,
    SuggestIndex,
    correct_spelling,
    did_you_mean,
    ranked_columns,
    relevance,
    smart_filters,
    sounds_like,
    tier,
)
from partylinks.security import SecurityMiddleware
from partylinks.service import ScrapeService, SourceBusyError
from partylinks.smartrails import rails as smartrails
from partylinks.venue_profiles import VenueProfile
from partylinks.venue_profiles import cached_profiles as cached_venue_profiles
from partylinks.venue_profiles import linked_names as linked_venue_names
from partylinks.venue_profiles import logo_path as venue_logo_path
from partylinks.venue_profiles import match as match_venue_profiles
from partylinks.venues import PLACE_LISTING
from partylinks.weather import cached_slots
from partylinks.weather import router as weather_router
from partylinks.worker.link import WorkerLink


# --------------------------------------------------------------------------- schemas
class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class TicketOut(_Out):
    """A ticket tier as it stands NOW: a sale window that closed (or has not opened)
    since the last scan is reflected immediately, without waiting for the next scan."""

    source_ticket_id: str | None
    name: str
    description: TidyText
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
    code: str | None = None  # the short link /e/<code> (partylinks.shortlinks); None: not given one yet
    source: str
    source_event_id: str
    # Our /go/ redirect, not the seller's link (see seo.go_path).
    url: str
    title: str
    start_at: datetime | None
    end_at: datetime | None
    time_known: bool = True
    time_zone: str | None = None
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
    # Set only on a paid card at the top of the results (GET /events?sponsored=true, see partylinks.ads):
    # the placement's event id, for /events/sponsored/hit. Such a card must say "Sponsored" (by law).
    sponsored: str | None = None
    # A search of names found it only in the description (or address), e.g. an artist named in a bio.
    in_text: bool = False
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
    likes: int = 0  # saved hearts (partylinks.popularity)
    views: int = 0  # visitors who opened its page, once a day each
    # A place's listing (a bar's happy hour, an escape room), one card per place: partylinks.placecards.
    place: bool = False
    deal: str | None = None  # the place's offer: "1+1 על שתייה"
    price_level: int | None = None  # the place's prices, ₪ to ₪₪₪₪ as 1-4
    rating: float | None = None  # the place's rating and its reviews, Google's as HappyTLV shows them
    reviews: int | None = None
    hood: str | None = None  # its neighbourhood, "הצפון הישן"
    bookable: bool = False  # matched to an Ontopo venue (partylinks.placeinfo)
    was_price: float | None = None  # the price before a recent drop (partylinks.smartrails)
    pinned: bool = False  # pinned by the owner to the home rows: shown first (partylinks.home_pins)
    is_sponsored: bool = (
        False  # paid right now: pinned or a sponsored placement (events.is_sponsored, partylinks.ads.paid)
    )
    weekly: str | None = None  # a weekly line's weekday, "thursday" (partylinks.lines)

    @model_validator(mode="after")
    def _go(self) -> EventSummaryOut:
        self.url = seo.go_path(self.id, self.code)
        return self


class DealOut(_Out):
    """The cheapest listing of an event across all sources (free entry wins)."""

    id: str
    code: str | None = None
    source: str
    url: str
    min_price: float | None
    has_free_entry: bool
    entry_type: str

    @model_validator(mode="after")
    def _go(self) -> DealOut:
        self.url = seo.go_path(self.id, self.code)
        return self


class AlternativeOut(_Out):
    """Another listing of the same event: different seller, link, prices and tiers."""

    id: str
    slug: str | None
    code: str | None = None
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
    resale: bool = False  # second-hand marketplace (viagogo, SwapTix, Ticket Place): extra.resale

    @model_validator(mode="after")
    def _go(self) -> AlternativeOut:
        self.url = seo.go_path(self.id, self.code)
        return self


class EventOut(EventSummaryOut):
    description: TidyText  # rows stored before tidying came in
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
    watching: int = 0  # visitors on its page in the last few minutes (0 below popularity.min_watching)
    # Lineup name -> its music / social links [{service, url}] (partylinks.artist_links); names without links left out.
    lineup_links: dict[str, list[dict[str, str]]] = {}
    # Lineup name -> its photo, from the artist profiles ticket sites curate (partylinks.artistdb); names without one left out.
    lineup_images: dict[str, str] = {}
    description_layout: DescriptionLayout | None = None  # the description in paragraphs, headings, lists


class EventPage(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[EventSummaryOut]
    # A search that found nothing: the closest known name that finds something ("did you mean").
    did_you_mean: str | None = None
    # A search that found nothing, widened as the bot does: "date" or "place" was dropped, and these
    # events are what ``widened_q`` finds (the other filters kept).
    widened: str | None = None
    widened_q: str | None = None


class RailsOut(BaseModel):
    """The home page's rows under "Trending": a pool per row (the page shows a random dozen)."""

    rails: dict[str, list[EventSummaryOut]] = {}
    holiday: HolidayOut | None = None  # the coming holiday its "holiday" row is about (partylinks.holiday_pages)


class MapEventOut(_Out):
    """A pin on the map: only what its popup shows."""

    id: str
    slug: str | None
    code: str | None = None
    title: str
    start_at: datetime | None
    end_at: datetime | None = None  # "on now" pins (map/live)
    time_known: bool = True
    time_zone: str | None = None
    local_date: date | None = None
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
    total: int  # upcoming real events across all stores, each once (one sold on three sites counts 1, not 3)
    box_offices: list[BoxOfficeOut] = []  # each Smarticket tenant (a city's hall, a council) as its own store


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
    members: list[str] | None = None  # genre families (the filter's groups): their genres, in order


def venue_facets(
    facets: list[FacetValue],
    venue_area: dict[str, str],
    rows: Sequence[Any],
    profiles: list[VenueProfile],
    linked: dict[str, str],
) -> list[FacetValue]:
    """Venue counts with their area, and the logo / links of the venues a ticket site curates."""
    spots: dict[str, tuple[float, float] | None] = {f.value: None for f in facets}
    for r in rows:
        if r.venue_name and r.latitude is not None and spots.get(r.venue_name) is None:
            spots[r.venue_name] = (r.latitude, r.longitude)
    found = match_venue_profiles(profiles, spots, linked)
    out = []
    for f in facets:
        update: dict[str, Any] = {"area": venue_area.get(f.value)}
        if p := found.get(f.value):
            update |= {
                "verified": True,
                "image_url": venue_logo_path(p),
                "profile_url": p.profile_url,
                "links": p.links or None,
            }
        out.append(f.model_copy(update=update))
    return out


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
    # The genre filter's families, in screen order (counted once per event), and the umbrella genres'
    # sub-genres.
    genre_groups: list[FacetValue] = []
    genre_families: dict[str, list[str]] = {}


class RetentionOut(BaseModel):
    removed_events: int


class EventSort(StrEnum):
    start_asc = "start_at"
    start_desc = "-start_at"
    price_asc = "min_price"
    price_desc = "-min_price"
    seen_desc = "-last_seen_at"
    trending = "-group_size"  # listed on the most ticket sites first


_IS_PLACE = func.coalesce(EventRow.extra[PLACE_LISTING].as_boolean(), False)


def _merged_cities(rows: Iterable[Any]) -> list[tuple[str, int]]:
    """Cities with their event counts, busiest first. A city abroad named in two languages by different
    sellers ("Paris" / "פריז") is one entry, shown in its most used spelling."""
    counts: Counter[tuple[str, Any]] = Counter()
    pairs = Counter((r.city, r.country) for r in rows if r.city)
    short = _short_cities(pairs)
    for (city, country), n in pairs.items():
        counts[(short.get((city, country), city), country)] += n
    spellings: dict[Any, Counter[str]] = {}
    for (city, country), n in counts.items():
        found = city_abroad(city, country) if country else None
        shown = city_name(city, country) if found else city  # rows stored before the name was cleaned
        spellings.setdefault(found[0] if found else city.casefold(), Counter())[shown] += n
    merged = [(names.most_common(1)[0][0], sum(names.values())) for names in spellings.values()]
    return sorted(merged, key=lambda c: -c[1])


def _city_heads(city: str) -> list[str]:
    """The shorter names of a city named with its river or region: "Ludwigshafen am Rhein" -> "Ludwigshafen",
    "Frankfurt / Main" -> "Frankfurt" (a seller stores the short name; "my location" may give the long one).
    Only before a lower-case word or a separator: "Frankfurt Oder" is another city than "Frankfurt"."""
    words = re.split(r"(\s+|[/(,])", city.strip())
    for i in range(2, len(words), 2):
        word = words[i - 1].strip() or words[i]
        if word and (word[0] in "/(," or word[0].islower()):
            head = "".join(words[: i - 1]).strip(" -")
            return [head] if len(head) >= 3 else []
    return []


def _short_cities(pairs: Iterable[tuple[str, Any]]) -> dict[tuple[str, Any], str]:
    """(city, country) -> the short name the same country's events also use, abroad only: "Ludwigshafen am Rhein"
    -> "Ludwigshafen" when some events say "Ludwigshafen" (one entry in the city box, and "my location" picks it)."""
    pairs = list(pairs)
    known = {(country, city.casefold()): city for city, country in pairs if country}
    found = {}
    for city, country in pairs:
        if country:
            for head in _city_heads(city):
                if (stored := known.get((country, head.casefold()))) is not None:
                    found[(city, country)] = stored
                    break
    return found


def _city_names(city: str, country: str | None) -> list[str]:
    """``city`` and, for a city abroad, its other names ("פריז" -> "Paris")."""
    found = city_abroad(city, country.upper() if country else None)
    return list(dict.fromkeys([city, *(found[1] if found else ())]))


# Missing values (no date, no price) sort as the smallest: first ascending, last descending.
_SORT_COLUMNS = {
    EventSort.start_asc: EventRow.start_at.asc().nulls_first(),
    EventSort.start_desc: EventRow.start_at.desc().nulls_last(),
    EventSort.price_asc: EventRow.group_min_price.asc().nulls_first(),
    EventSort.price_desc: EventRow.group_min_price.desc().nulls_last(),
    EventSort.seen_desc: EventRow.last_seen_at.desc().nulls_last(),
    EventSort.trending: EventRow.group_size.desc().nulls_last(),
}
_START = _SORT_COLUMNS[EventSort.start_asc]
RANK_MAX = 3000  # a search with more matches is listed by date, unranked (see list_events)


# --------------------------------------------------------------------------- app
class _SiteFiles(StaticFiles):
    """Static files with an SPA fallback: unknown paths get index.html (client-side routes)."""

    async def get_response(self, path: str, scope: Any) -> Response:
        try:
            return await super().get_response(path, scope)
        except OSError:  # a name the filesystem refuses ("<" on Windows, a component over 255 bytes): no such file
            pass
        except StarletteHTTPException as exc:
            if exc.status_code != status.HTTP_404_NOT_FOUND:
                raise
        response = await super().get_response("index.html", scope)
        response.status_code = status.HTTP_410_GONE  # client renders "not found"; crawlers drop it (gone_handler)
        return response


class CacheBusy(Exception):
    """No build slot freed within ``build_wait``: the request gets a quick 503 instead of holding a thread."""


class Uncached(Exception):
    """A builder's word that its page is no longer served at its address (an event merged into another's group
    answers 301 now): the cached copy goes, so the next request reaches the route instead of the old page."""


class ResponseCache:
    """Bodies of public reads (JSON, rendered pages). An entry goes stale when a write commits
    (``Database.changes``, unless ``follow_writes`` is off; never before ``min_fresh``, so a scrape committing
    every few seconds does not rebuild every page on every visit) or ``ttl`` passes ("upcoming" and "free until" move with the clock); a
    stale entry younger than ``max_stale`` is still served at once while a background thread
    rebuilds it, so only the first visit to a page (or one after a long quiet spell) waits for a build.

    ponytail: per-process dict, cleared when it outgrows ``max_bytes``; fine for the single uvicorn
    worker. Two rebuild threads: a burst of stale pages refreshes in turn, not all at once.
    """

    def __init__(
        self,
        ttl: float = 60,
        max_bytes: int = 64 << 20,
        max_stale: float = 3600,
        follow_writes: bool = True,
        min_fresh: float = 0,
        build_wait: float = 0,
    ) -> None:
        self.ttl, self.max_bytes, self.max_stale, self.follow_writes = ttl, max_bytes, max_stale, follow_writes
        self.min_fresh = min_fresh
        # A request waits this long for a build slot, then CacheBusy (0: as long as it takes). Crawlers opening
        # cold pages held all slots; every waiting request held a thread until uvicorn's 200 were full and the
        # whole site answered 503 (2026-11-12, twice).
        self.build_wait = build_wait
        self._items: dict[tuple[str, str], tuple[float, int, bytes, int]] = {}  # built at, changes, body, status
        self._builders: dict[tuple[str, str], Callable[[], bytes | tuple[bytes, int]]] = {}  # to refresh a peeked entry
        self._bytes = 0
        self._lock = threading.Lock()
        self._refreshing: set[tuple[str, str]] = set()
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cache-refresh")
        # A crawler opening dozens of cold pages at once builds them a few at a time, so the GIL is
        # left for the event loop serving cached pages (a link preview gives up after a few seconds).
        self._builds = threading.BoundedSemaphore(4)
        # Crawlers build cold pages on slots of their own: Yandex opening dozens of long-tail pages at once took
        # all four, and visitors behind it got 503s (2026-11-24). A visitor's page never waits on a crawler's.
        self._crawler_builds = threading.BoundedSemaphore(2)

    def peek(
        self, key: tuple[str, str], build: Callable[[], bytes | tuple[bytes, int]] | None = None
    ) -> tuple[bytes, int] | None:
        """(body, status) of ``key`` if cached, fresh or stale (a stale one is rebuilt in the
        background); None when it has to be built first. Never builds in the caller's thread."""
        if hit := self._items.get(key):
            with self._lock:  # least recently used last out: a hit moves to the end
                if key in self._items:
                    self._items[key] = self._items.pop(key)
            built_at, changes, body, code = hit
            age = time.monotonic() - built_at
            if age < self.ttl and (age < self.min_fresh or changes == Database.changes or not self.follow_writes):
                return body, code
            build = build or self._builders.get(key)
            if age < self.max_stale and build:
                self._refresh(key, build)
                return body, code
        return None

    def get(self, key: tuple[str, str], build: Callable[[], bytes | tuple[bytes, int]]) -> tuple[bytes, int]:
        """(body, status) of ``key``; ``build`` returns the body, or (body, status)."""
        if hit := self.peek(key, build):
            return hit
        try:
            return self._build(key, build, self.build_wait)
        except CacheBusy:
            if old := self._items.get(key):  # busy: any older copy at once (refreshed behind), not a 503
                self._refresh(key, build)
                return old[2], old[3]
            raise

    def _build(
        self, key: tuple[str, str], build: Callable[[], bytes | tuple[bytes, int]], wait: float = 0
    ) -> tuple[bytes, int]:
        slots = self._crawler_builds if wait and CRAWLING.get() else self._builds  # background refreshes: shared
        if not slots.acquire(timeout=wait or None):
            raise CacheBusy(key[0])
        try:
            changes, now = Database.changes, time.monotonic()  # read before building: a write during it wins
            out = build()
        except Uncached:
            self.forget(key)
            raise
        finally:
            slots.release()
        body, code = out if isinstance(out, tuple) else (out, 200)
        with self._lock:
            if old := self._items.pop(key, None):
                self._bytes -= len(old[2])
            # The least recently used go first. Clearing it all (as before) emptied the home page and every hot
            # page together whenever a crawler walk filled it: all cold at once, under load.
            while self._items and self._bytes + len(body) > self.max_bytes:
                gone = next(iter(self._items))
                self._bytes -= len(self._items.pop(gone)[2])
                self._builders.pop(gone, None)
            self._items[key] = (now, changes, body, code)
            self._builders[key] = build
            self._bytes += len(body)
        return body, code

    def body(self, key: tuple[str, str]) -> bytes | None:
        """The cached body of ``key``, fresh or stale, without refreshing it."""
        hit = self._items.get(key)
        return hit[2] if hit else None

    def forget(self, key: tuple[str, str]) -> None:
        with self._lock:
            if old := self._items.pop(key, None):
                self._bytes -= len(old[2])
            self._builders.pop(key, None)

    def save(self, path: Path, max_bytes: int) -> int:
        """Write the most recently used entries (up to ``max_bytes`` of bodies) to ``path``; returns how many.
        Each entry is a JSON header line (path, query, age in seconds, status, size) and then its body."""
        with self._lock:
            items = list(self._items.items())
        mono, wall, total, kept = time.monotonic(), time.time(), 0, []
        for key, (built_at, _, body, code) in reversed(items):  # the most recently used are last
            if total + len(body) > max_bytes:
                break
            total += len(body)
            kept.append((key, wall - (mono - built_at), body, code))
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        with gzip.open(part, "wb", compresslevel=1) as out:
            for (route, query), built, body, code in kept:
                head = {"path": route, "query": query, "built": built, "status": code, "size": len(body)}
                out.write(json.dumps(head).encode() + b"\n" + body)
        os.replace(part, path)  # a crash mid-write leaves the previous snapshot whole
        return len(kept)

    def load(self, path: Path) -> int:
        """Read a ``save`` snapshot back as stale entries: served at once, rebuilt in the background on their
        first visit (when a route hands over its builder). Entries past ``max_stale`` are dropped."""
        mono, wall, loaded = time.monotonic(), time.time(), 0
        with gzip.open(path, "rb") as src:
            entries = []
            while head := src.readline():
                meta = json.loads(head)
                body = src.read(meta["size"])
                if len(body) != meta["size"]:
                    break  # a cut-off file: what came before it is still good
                age = max(wall - meta["built"], self.ttl)  # stale, so the first visit rebuilds it
                if age < self.max_stale:
                    entries.append(((meta["path"], meta["query"]), mono - age, body, meta["status"]))
        with self._lock:
            fits, room = [], self.max_bytes - self._bytes
            for entry in entries:  # most recently used first get the room
                if entry[0] not in self._items and len(entry[2]) <= room:  # a page built since startup is newer
                    fits.append(entry)
                    room -= len(entry[2])
            for key, built_at, body, code in reversed(fits):  # stored least recently used first
                self._items[key] = (built_at, -1, body, code)
                self._bytes += len(body)
                loaded += 1
        return loaded

    def _refresh(self, key: tuple[str, str], build: Callable[[], bytes | tuple[bytes, int]]) -> None:
        with self._lock:
            if key in self._refreshing:
                return
            self._refreshing.add(key)

        def run() -> None:
            try:
                self._build(key, build)
            except Uncached:
                pass  # forgotten: the next request is routed
            except Exception:
                log.exception("cache refresh of %s failed", key[0])
            finally:
                with self._lock:
                    self._refreshing.discard(key)

        self._pool.submit(run)


def snapshot_caches(app: Any, settings: Settings, action: str) -> None:
    """Save or load (``action``) the JSON and rendered-page caches under ``api.cache_snapshot_dir``. Never
    raises: without a snapshot the site only starts cold, as before."""
    if not settings.api.cache_snapshot_dir:
        return
    folder = Path(settings.api.cache_snapshot_dir)
    for name in ("response_cache", "html_cache"):
        cache: ResponseCache | None = getattr(app.state, name, None)
        if cache is None:
            continue
        path = folder / f"{name}.gz"
        started = time.monotonic()
        try:
            if action == "save":
                count = cache.save(path, settings.api.cache_snapshot_mb << 20)
            elif path.exists():
                count = cache.load(path)
            else:
                continue
        except Exception:
            log.exception("cache snapshot: %s of %s failed", action, name)
            continue
        log.info("cache snapshot: %s %d %s entries in %.1fs", action, count, name, time.monotonic() - started)


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
            for cache, media, k in ((getattr(state, "html_cache", None), b"text/html; charset=utf-8", seo.html_key(*key)),
                                    (state.response_cache, b"application/json", key)):  # fmt: skip
                if cache and (hit := cache.peek(k)):
                    body, code = hit
                    size = str(len(body)).encode()
                    # x-body-length: the size before gzip (which rewrites content-length), as map_events
                    # sends it: the map's loader counts the bytes against it for its real percent.
                    await send({"type": "http.response.start", "status": code, "headers": [
                        (b"content-type", media), (b"content-length", size), (b"x-body-length", size),
                    ]})  # fmt: skip
                    return await send({"type": "http.response.body", "body": body})
        if scope["type"] == "http":  # a page this request builds takes a crawler's slot or a visitor's
            agent = dict(scope.get("headers") or []).get(b"user-agent", b"").decode("latin-1")
            CRAWLING.set(is_crawler(agent))
        await self.app(scope, receive, _body_length(send) if scope["type"] == "http" else send)


# Set per request by CachedPages; the sync routes' threads see it (Starlette copies the context into them).
CRAWLING: ContextVar[bool] = ContextVar("crawling", default=False)
_CRAWLER_AGENT = re.compile(r"bot|crawl|spider|slurp|externalagent|lightpanda|headless", re.IGNORECASE)
# Link previews fetch a page because a person just shared it: they stay on the visitors' slots.
_PREVIEW_AGENT = re.compile(
    r"whatsapp|telegrambot|facebookexternalhit|twitterbot|slackbot|discordbot|linkedinbot|skypeuripreview",
    re.IGNORECASE,
)


def is_crawler(agent: str) -> bool:
    return bool(_CRAWLER_AGENT.search(agent)) and not _PREVIEW_AGENT.search(agent)


def _body_length(send: Any) -> Any:
    """``send`` adding x-body-length (as a cache hit has it) to a routed JSON answer with a content-length:
    every page's loader counts the bytes against it for its real percent (web/src/api.ts getJSONProgress)."""

    async def sized(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            headers = {bytes(k).lower(): bytes(v) for k, v in message.get("headers", [])}
            size = headers.get(b"content-length")
            if (
                size
                and b"x-body-length" not in headers
                and headers.get(b"content-type", b"").startswith(b"application/json")
            ):
                message = {**message, "headers": [*message.get("headers", []), (b"x-body-length", size)]}
        await send(message)

    return sized


def _cached(request: Request, build: Callable[[Session], BaseModel], *, exclude_none: bool = False) -> Response:
    """The endpoint's JSON from the response cache. ``build`` gets its own session: it may run
    after the request, refreshing a stale entry in the background."""
    cache: ResponseCache = request.app.state.response_cache
    db: Database = request.app.state.db

    def body() -> bytes:
        with db.session(write=False) as session:
            return build(session).model_dump_json(by_alias=True, exclude_none=exclude_none).encode()

    return Response(cache.get((request.url.path, request.url.query), body)[0], media_type="application/json")


def _name_index(request: Request) -> NameIndex:
    """Lineup and venue names by their skeleton, and the words of every title and name (spelling fixes),
    rebuilt at most every api.sound_index_ttl_seconds (10 minutes): in the background once built, so no
    search waits for the rebuild (a read of every primary event's names, ~1 s on prod)."""
    state = request.app.state
    built, index = getattr(state, "sound_index", (0.0, None))
    if index is None:
        return _build_name_index(state)
    stale = time.monotonic() - built > get_settings().api.sound_index_ttl_seconds
    if stale and not getattr(state, "sound_index_building", False):
        state.sound_index_building = True
        threading.Thread(target=_build_name_index, args=(state,), name="name-index", daemon=True).start()
    return index


def _build_name_index(state: Any) -> NameIndex:
    try:
        db: Database = state.db
        with db.session(write=False) as session:
            names = select(EventRow.lineup, EventRow.venue_name, EventRow.title).where(EventRow.is_primary.is_(True))
            rows = session.execute(names).all()
        index = NameIndex.of(rows)
        state.sound_index = (time.monotonic(), index)
        return index
    finally:
        state.sound_index_building = False


def _sound_index(request: Request) -> dict[str, list[str]]:
    """Every lineup and venue name by its skeleton (``_name_index``)."""
    return _name_index(request).by_skeleton


def _sounds_like(request: Request, q: str) -> list[str]:
    """Lineup names that sound like ``q`` in another spelling: "אינפקטד מאשרום" finds Infected Mushroom."""
    return sounds_like(q, _sound_index(request))


# Built in the background on startup (an empty cache made the first visits after a deploy take 3-12 s).
# The map's pins with the exact query its page sends (MapPage: filterParams sorted by start, api.ts useMapEvents
# compact): "/events/map" alone was another cache key, so the first visitor after a deploy waited 7-17 s.
MAP_WARM_PATH = "/events/map?sort=start_at&compact=true"

WARM_PATHS = (
    "/",
    "/artists",
    "/venues",
    "/stores",
    "/tonight",
    "/weekend",
    "/map",
    "/abroad",
    "/blog",
    "/posts",
    "/posts?lang=en",
    "/categories",
    "/categories?anywhere=true",
    # The abroad page's country and city boxes (Abroad.tsx, its exact query): 14 s cold over 120k events (25.11.2026).
    "/categories?abroad=true&lists=false",
    "/ticket-sites",
    "/events/rails",
    "/sitemap.xml",
)


def build_event_filters(
    cfg: Settings,
    *,
    sounds_like: Sequence[str] = (),
    source: list[str] | None = None,
    genre: list[str] | None = None,
    artist: str | None = None,
    venue: str | None = None,
    venue_slug: str | None = None,
    city: str | None = None,
    in_target_city: bool | None = None,
    abroad: bool | None = None,
    country: str | None = None,
    anywhere: bool = False,
    entry_type: list[EntryType] | None = None,
    free_entry: bool | None = None,
    sold_out: bool | None = None,
    discount: bool | None = None,
    day_of_week: list[str] | None = None,
    time_slot: list[str] | None = None,
    area: list[str] | None = None,
    region: list[str] | None = None,
    weekend: bool | None = None,
    on_date: date | None = None,
    open_now: bool | None = None,
    upcoming: bool = True,
    include_duplicates: bool = False,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    q: str | None = None,
) -> list[ColumnElement[bool]]:
    """WHERE clauses of the event filters: /events and /events/map (through ``event_filters``) and the
    alert matcher, which has no request. ``sounds_like``: lineup names that sound like ``q``."""
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
        filters.append(lineup_has(artist))
    if venue:
        # The venue's page: that name only, any case ("היכל התרבות" is not every היכל התרבות in the country).
        filters.append(seo.like_contains(EventRow.venue_name, venue, whole=True))
    if venue_slug:
        filters.append(EventRow.venue_slug == venue_slug)
    if city:
        names = _city_names(city, country)
        filters.append(or_(
            *(cond for name in names
              for cond in (EventRow.city.ilike(_contains(name), escape="\\"), EventRow.address.ilike(_contains(name), escape="\\"))),
            # Abroad only (a country chosen; Israel's search is untouched): "Ludwigshafen am Rhein" is also the
            # events a seller stored as "Ludwigshafen" (that name exactly).
            *(EventRow.city.ilike(_contains(head)[1:-1], escape="\\")
              for name in (names if country else []) for head in _city_heads(name)),
        ))  # fmt: skip
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
    if open_now:
        filters += open_now_filters(tz)
    if upcoming:
        current = upcoming_filter()
        if q and q.strip() and cfg.retention.undated_sources:  # a search also finds those sources' undated offers
            undated = and_(
                EventRow.start_at.is_(None),
                EventRow.is_listed.is_not(False),
                EventRow.source.in_(cfg.retention.undated_sources),
            )
            current = or_(current, undated)
        filters.append(current)
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
        filters += smart_filters(q, cfg, sounds_like)
    return filters


def create_app(
    settings: Settings | None = None,
    start_scheduler: bool | None = None,
    worker_process: bool = False,
    overrides: bool = False,
) -> FastAPI:
    """``worker_process``: the scheduler and sources run in the worker process (partylinks/worker);
    this one serves, and reads the worker's state / sends it the admin's commands."""
    # The app's own settings (not a test's): the admin's overrides apply. `serve` passes the file's settings
    # and overrides=True; without it the web process ignored the overrides until an admin "apply".
    from_config = settings is None or overrides
    settings = settings or get_settings()
    setup_logging(settings.logging)  # also for `serve --reload` workers, which never run cli.main()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        nonlocal settings
        db = Database(settings.database.url, settings.database.echo, settings.database)
        db.create_schema()
        if from_config:
            settings = config_overrides.load(db)  # settings.yaml + the admin's overrides from the database
        service = ScrapeService(settings, db, owns_runs=not worker_process)
        if not worker_process:
            service.recompute_areas()  # stored events follow the configured areas (the worker does it otherwise)
        run_scheduler = settings.scheduler.enabled if start_scheduler is None else start_scheduler
        scheduler = build_scheduler(settings, service) if run_scheduler else None
        app.state.settings, app.state.db, app.state.service, app.state.scheduler = settings, db, service, scheduler
        app.state.worker = WorkerLink(db, settings.scheduler) if worker_process else None
        app.state.tasks = set()

        async def maintenance() -> None:
            """Before any source writes: artwork still in the database moves to files. Then genres are re-read if their config changed, the scrapers start, and map coordinates are
            filled for address-only events."""
            jobs: list[Callable[[], object]] = [service.media.move_to_files, service.recompute_genres]
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
                # The JSON the first screens fetch, with the exact query the web app sends (the cache is keyed
                # by it): the venues / artists index's first page and the map's pins (7-8 s cold after a deploy).
                size = settings.site.web.index_page_size
                api = [f"/facets/{kind}?page_size={size}&page=1" for kind in ("venues", "artists")] + [MAP_WARM_PATH]
                for path in (*WARM_PATHS, *api):
                    try:
                        await client.get(path)
                    except Exception:  # a cold page is only slower
                        log.exception("cache warm-up of %s failed", path)

        async def warm_filters() -> None:
            """The filters' counts kept warm (api.warm_filters_minutes): the select boxes of the home page, of
            /abroad and of its busiest countries never wait for a cold build (2-12 s each, 25.11.2026)."""
            every = settings.api.warm_filters_minutes * 60
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
                while True:
                    await asyncio.sleep(every)
                    try:
                        abroad = await client.get("/categories?abroad=true&lists=false")
                        countries = [c["value"] for c in abroad.json().get("countries", [])][
                            : settings.api.warm_countries
                        ]
                        for path in [
                            "/categories",
                            MAP_WARM_PATH,
                            "/events/rails",  # the home rows: refreshed in turns (home_rails.RowCache), never cold
                            *(f"/categories?abroad=true&country={c}&lists=false" for c in countries),
                        ]:
                            await client.get(path)  # one at a time: never more than one build slot
                    except Exception:  # a cold filter is only slower
                        log.exception("filter warm-up failed")

        async def snapshots() -> None:
            """The cached pages saved every cache_snapshot_minutes: a crash, not only a deploy, keeps them."""
            while True:
                await asyncio.sleep(settings.api.cache_snapshot_minutes * 60)
                await asyncio.to_thread(snapshot_caches, app, settings, "save")

        visitors = run_scheduler or worker_process  # the server that serves visitors
        if visitors:  # last deploy's pages, before the first request
            await asyncio.to_thread(snapshot_caches, app, settings, "load")
        jobs = [] if worker_process else [maintenance()]  # the worker runs the startup passes itself
        if visitors:
            jobs.append(warm())
            if settings.api.warm_filters_minutes:
                jobs.append(warm_filters())
            if settings.api.cache_snapshot_minutes:
                jobs.append(snapshots())
        if app.state.worker:
            jobs.append(app.state.worker.follow_writes())
        for job in jobs:
            task = asyncio.create_task(job)
            app.state.tasks.add(task)
            task.add_done_callback(app.state.tasks.discard)
        try:
            server_status.log_startup(settings, db, scheduler, app.state.worker)
        except Exception:  # the banner never blocks startup
            log.exception("status: startup report failed")
        if settings.logging.status_every_minutes:
            beat = asyncio.create_task(server_status.status_loop(settings, db, scheduler, app.state.worker))
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
            if visitors:
                snapshot_caches(app, settings, "save")
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

    @app.exception_handler(StarletteHTTPException)
    async def _gone(request: Request, exc: StarletteHTTPException) -> Response:
        # Nothing at this address is "gone" (410), not "not found" (404), everywhere: an event that is
        # over is deleted, and 410 tells search engines to drop its links for good instead of retrying.
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            exc = StarletteHTTPException(status.HTTP_410_GONE, exc.detail, exc.headers)
        return await http_exception_handler(request, exc)

    @app.exception_handler(DataError)
    async def _bad_value(request: Request, exc: DataError) -> JSONResponse:
        # A value PostgreSQL refuses (a NUL byte in a string, a number past bigint): the request's
        # fault, whichever route took it in; 400, not a 500 that reads as the site being down.
        log.info("request: %s %s refused by the database: %s", request.method, request.url.path, exc.orig)
        return JSONResponse({"detail": "invalid value"}, status_code=status.HTTP_400_BAD_REQUEST)

    @app.exception_handler(RequestValidationError)
    async def _invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Which field and why, never the value sent back or the accepted values (``input`` / ``ctx``): a probe
        # walking the query parameters (?sort=x, ?country=DEU) learnt the API's enums from the echo. A form's
        # body keeps its message (the /submit and /contact forms show it); a query parameter only "invalid value".
        errors = [
            {
                "loc": list(e.get("loc", ())),
                "msg": e.get("msg", "") if e.get("loc", ("",))[0] == "body" else "invalid value",
            }
            for e in exc.errors()
        ]
        return JSONResponse({"detail": errors}, status_code=status.HTTP_422_UNPROCESSABLE_ENTITY)

    @app.exception_handler(CacheBusy)
    async def _busy(request: Request, exc: CacheBusy) -> Response:
        log.info("busy: %s waited %ss for a build slot", request.url.path, settings.api.cache_build_wait_seconds)
        return JSONResponse({"detail": "busy, try again shortly"}, status_code=503, headers={"Retry-After": "30"})

    app.state.response_cache = ResponseCache(
        ttl=settings.api.cache_ttl_seconds, min_fresh=settings.api.cache_min_fresh_seconds,
        build_wait=settings.api.cache_build_wait_seconds, max_stale=settings.api.json_max_stale_seconds,
    )  # fmt: skip
    app.state.home_rows = RowCache()  # each home row's cards (/events/rails), refreshed in turns
    sign_in_state(app, settings)  # innermost: only the Google sign-in routes read it
    app.add_middleware(CachedPages)  # inside gzip and security: a cached page is still compressed and guarded
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    # CORS: only the site itself may read the API from a browser (the app is same-origin anyway);
    # other sites' pages get no Access-Control-Allow-Origin, so their reads are blocked.
    app.add_middleware(CORSMiddleware, allow_origins=[settings.site.base_url.rstrip("/")], allow_methods=["GET"])
    app.add_middleware(SecurityMiddleware)  # outermost: rejects before any work is done
    app.include_router(saved_router)  # saved events; before /events/{event_ref} so /events/cards is not an event
    app.include_router(artwork_router)  # drawn cards for events with no picture
    app.include_router(mail_images_router)  # JPEG crops of the pictures, for emails
    _register_routes(app)
    app.include_router(admin_router)
    from partylinks.artist_links.routes import router as artist_links_admin  # local: it imports partylinks.admin

    app.include_router(artist_links_admin)  # the maintenance tab's artist-links counts
    from partylinks.admin_insights.routes import router as insights_admin  # local: it imports partylinks.admin

    app.include_router(insights_admin)  # the admin's features and quality tabs
    app.include_router(weather_router)
    install_accounts(app, settings)  # optional Google accounts: /auth, /api/me, the admin users tab
    install_bot(app)  # the Telegram bot's webhook (partylinks.bot); off until its secrets are set
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
# Closed when the endpoint returns, not after the body is sent: a FileResponse reads the file on the
# shared thread pool, and a burst of image requests holding connections while waiting for those
# threads (pool size == thread count) used to deadlock until pool_timeout (500s on the home page).
ReleasedSessionDep = Annotated[Session, Depends(get_session, scope="function")]


def _best_deals(session: Session, rows: Sequence[EventRow]) -> dict[str, DealOut]:
    """Cheapest listing per event, loaded in one query for a whole page."""
    ids = {r.best_listing_id for r in rows if r.best_listing_id}
    if not ids:
        return {}
    deal_columns = load_only(
        EventRow.id,
        EventRow.code,
        EventRow.source,
        EventRow.url,
        EventRow.min_price,
        EventRow.has_free_entry,
        EventRow.entry_type,
    )
    return {
        d.id: DealOut.model_validate(d)
        for d in session.scalars(select(EventRow).where(EventRow.id.in_(ids)).options(deal_columns))
    }


_IN_CHUNK = 20_000  # values per IN list: a query takes at most 65,535 parameters


def _chunks(items: list[str], size: int) -> Iterator[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _utc_seconds(value: datetime | None) -> int | None:
    return int(_aware_utc(value).timestamp()) if value is not None else None


def map_columns(rows: Sequence[Any], unplaced: int, sources: dict[str, list[str]]) -> dict[str, Any]:
    """The map's pins as columns (GET /events/map?compact=true): the same values as MapEventOut, a fifth of
    the bytes. 68k pins were 32 MB as objects (5.7 MB gzipped; 25.11.2026): every key repeated per pin, the
    slug (only needed without a short code: eventPath), and the same venue / zone / ticket-site names over and
    over. Repeated strings are indexes into ``venues`` / ``zones`` / ``site_sets``; times are Unix seconds
    (UTC); a point keeps 5 decimals (about a metre). web/src/api.ts mapPins expands it back."""
    tables: dict[str, dict[Any, int]] = {"venues": {}, "zones": {}, "site_sets": {}}

    def index(table: str, value: Any) -> int | None:
        if value is None:
            return None
        return tables[table].setdefault(value, len(tables[table]))

    out = []
    for r in rows:
        out.append([
            r.id, r.code, None if r.code else r.slug, r.title,
            _utc_seconds(r.start_at), _utc_seconds(r.end_at), 0 if r.time_known is False else 1,
            index("zones", r.time_zone), r.local_date.isoformat() if r.local_date else None, index("venues", r.venue_name),
            round(r.latitude, 5), round(r.longitude, 5), index("site_sets", tuple(sources.get(r.id) or [r.source])),
        ])  # fmt: skip
    return {
        "fields": [
            "id",
            "code",
            "slug",
            "title",
            "start",
            "end",
            "time_known",
            "zone",
            "local_date",
            "venue",
            "lat",
            "lng",
            "sites",
        ],
        "rows": out,
        "venues": list(tables["venues"]),
        "zones": list(tables["zones"]),
        "site_sets": [list(t) for t in tables["site_sets"]],
        "unplaced": unplaced,
    }


def _group_sources(session: Session, rows: Sequence[EventRow]) -> dict[str, list[str]]:
    """Ticket sites per duplicate group, loaded in one query for a whole page."""
    groups = sorted({r.group_id for r in rows if r.group_id})
    found: dict[str, set[str]] = {}
    # In chunks: the map's every event abroad and in Israel passed Postgres's 65,535 parameters (25.11.2026, a 500).
    for chunk in _chunks(groups, _IN_CHUNK):
        for group_id, source in session.execute(
            select(EventRow.group_id, EventRow.source).where(EventRow.group_id.in_(chunk)).distinct()
        ):
            if group_id:
                found.setdefault(group_id, set()).add(source)
    return {r.id: [r.source, *sorted(found.get(r.group_id or "", set()) - {r.source})] for r in rows}


def _group_covers(session: Session, rows: Sequence[EventRow]) -> dict[str, str]:
    """A duplicate listing's stored picture for events with no stored picture of their own: an own URL
    that never downloaded (a favicon, a 403) used to block it (partylinks.pictures)."""
    groups = sorted({r.group_id for r in rows if r.group_id and not r.cover_url})
    found: dict[str, str] = {}
    for chunk in _chunks(groups, _IN_CHUNK):  # under Postgres's 65,535 parameters (as _group_sources)
        for group_id, image_id, url in session.execute(
            select(EventRow.group_id, ImageRow.id, ImageRow.url)
            .join(ImageRow.event)
            .where(EventRow.group_id.in_(chunk), ImageRow.downloaded_at.is_not(None))
            .order_by(ImageRow.position, ImageRow.id)
        ):
            if group_id and not is_site_logo(url):
                found.setdefault(group_id, f"/images/{image_id}")
    return found


def _summary_extras(
    session: Session, rows: Sequence[EventRow], cfg: Settings | None = None
) -> dict[str, dict[str, Any]]:
    cfg = cfg or get_settings()
    deals = _best_deals(session, rows)
    sources = _group_sources(session, rows)
    covers = _group_covers(session, rows)
    # Only with no picture of its own or its group's: a real picture always wins (artwork package).
    # Own stored > duplicate's stored > own URL still loading > drawn card (partylinks.pictures).
    drawn = artwork.urls(
        session, [r.id for r in rows if not (r.cover_url or covers.get(r.group_id or "") or live_image_url(r))]
    )
    liked = popularity.counts(session, [r.id for r in rows])  # likes and views
    places = placecards.card_extras(session, rows)
    was = smartrails.was_prices(session, cfg, rows, datetime.now(UTC))
    weekly = lines_weekly(session, cfg, rows)
    return {
        r.id: {
            **liked[r.id],
            **places.get(r.id, {}),
            "was_price": was.get(r.id),
            "weekly": weekly.get(r.id),
            "best_deal": deals.get(r.best_listing_id) if r.best_listing_id else None,
            "sources": sources[r.id],
            "cover_url": r.cover_url or covers.get(r.group_id or "") or drawn.get(r.id),
            "image_url": live_image_url(r),  # a failed download or a logo: none
        }
        for r in rows
    }


log = logging.getLogger(__name__)

CRAWLER = re.compile(r"bot|crawler|spider|google-", re.IGNORECASE)  # search crawlers following /go/, not clicks
MAX_LIST_PARAMS = 20


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
    """Any-of match on a JSON string array (its JSONB text holds each string quoted)."""
    return or_(*(cast(column, String).ilike(f'%"{v}"%') for v in values))


def _home_row_filters(
    filters_of: Callable[..., list[ColumnElement[bool]]], cfg: Settings, spec: HomeRail, now: datetime
) -> list[ColumnElement[bool]]:
    """A home_rails row's WHERE clauses: the API's event filters (``filters_of``) and the row's own conditions."""
    window = spec.window(now.date())  # a holiday's or a month's next dates, however far
    filters = filters_of(
        date_to=now + timedelta(days=366 if window else spec.days), genre=spec.genre or None,
        time_slot=spec.time_slot or None, free_entry=spec.free_entry or None,
        on_date=now.date() if spec.today else None, weekend=spec.weekend or None,
        abroad=spec.abroad or None, discount=spec.discount or None, max_price=spec.max_price,
    )  # fmt: skip
    if window:
        filters += [EventRow.local_date >= window[0], EventRow.local_date <= window[1]]
    if spec.max_price:
        filters.append(EventRow.group_min_price > 0)
    if spec.multi:
        filters.append(EventRow.group_size > 1)
    if spec.places is not None:
        filters.append(placecards.IS_PLACE if spec.places else ~placecards.IS_PLACE)
    if spec.exclude:
        filters.append(not_(_json_contains(EventRow.genres, cfg.enrichment.expand_genres(spec.exclude))))
    return filters


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
            "accessibility": site.accessibility.model_dump(mode="json")
            | {"email": hide_email(site.accessibility.email)},
            "privacy": site.privacy.model_dump(mode="json") | {"email": hide_email(site.privacy.email)},
            "accounts": request.app.state.settings.accounts.ready,  # any account UI at all
            "providers": request.app.state.settings.accounts.providers,  # the "Continue with ..." buttons
            "telegram": telegram_username(request.app),  # the bot's @name (web/src/bot/TelegramFab.tsx)
            "card_sources_shown": site.card_sources_shown,  # names on a card before "+N" (web/src/card_stores)
            "passkey_offer_days": site.passkey_offer_days,  # web/src/passkeys/PasskeyOffer.tsx
            "web": site.web.model_dump(),  # web/src/site_params/useWeb.ts
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
        except Exception as exc:  # noqa: BLE001 - health reports any database failure as "down", never raises
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            report["status"] = "down"
            report["checks"]["database"] = f"error: {type(exc).__name__}"
            return report
        scheduler, worker = state.scheduler, state.worker
        worker_state = worker.state() if worker else None
        if worker:  # the scheduler runs in the worker process: its heartbeat says how it is
            report["checks"]["scheduler"] = (
                "down (no worker heartbeat)"
                if not worker_state or not worker_state["alive"]
                else worker_state["scheduler"]
            )
        else:
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
        if worker:
            retention_now = (worker_state or {}).get("retention") or {}
        else:
            r = state.service.retention_state
            retention_now = {"last_success_at": r.last_success_at, "last_error": r.last_error}
        report["checks"]["retention"] = {
            "last_success_at": retention_now.get("last_success_at"),
            "last_error": retention_now.get("last_error"),
        }
        scheduler_down = (
            not (worker_state and worker_state["alive"]) if worker else scheduler is not None and not scheduler.running
        )
        if scheduler_down or report["checks"]["config_errors"] or retention_now.get("last_error"):
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
            str | None,
            Query(min_length=2, max_length=MAX_TEXT_PARAM, description="Venue name (the whole name, any case)"),
        ] = None,
        venue_slug: Annotated[str | None, Query(description="Exact venue slug")] = None,
        city: Annotated[str | None, Query(min_length=2, max_length=MAX_TEXT_PARAM, description="City contains")] = None,
        in_target_city: Annotated[bool | None, Query(description="Matched to the configured target city")] = None,
        abroad: Annotated[
            bool | None,
            Query(
                description="Events abroad instead of events in Israel (default: Israel, both for an artist or venue)"
            ),
        ] = None,
        country: Annotated[
            str | None, Query(min_length=2, max_length=2, description="Country code (ISO 3166 alpha-2), events abroad")
        ] = None,
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
        open_now: Annotated[
            bool | None, Query(description="Started and not over yet (a bar's happy hour, a party in progress)")
        ] = None,
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
        params = dict(locals())  # every query parameter, by name
        del params["request"]
        return build_event_filters(
            request.app.state.settings, sounds_like=_sounds_like(request, q) if q else (), **params
        )

    # A default, not Annotated: string annotations (``from __future__``) cannot see this local function.
    event_filters_dep = Depends(event_filters)

    @app.get("/events", response_model=EventPage, tags=["events"])
    def list_events(
        request: Request,
        filters: list[ColumnElement[bool]] = event_filters_dep,
        sort: EventSort = EventSort.start_asc,
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int | None, Query(ge=1)] = None,
        sponsored: Annotated[
            bool, Query(description="Paid placements matching these filters first, marked (the site's own lists)")
        ] = False,
        background: BackgroundTasks = BackgroundTasks(),  # noqa: B008 - FastAPI injects the request's tasks
    ) -> Response:
        cfg: Settings = request.app.state.settings
        size = min(page_size or cfg.api.default_page_size, cfg.api.max_page_size)
        if page > cfg.api.max_page:  # no paging through every event: narrow the filters instead
            raise HTTPException(400, f"page is at most {cfg.api.max_page}")

        # A search in the default date order puts title matches first, then lineup / venue, then the rest.
        q = request.query_params.get("q")
        rank = relevance(q, cfg, _sounds_like(request, q)) if q and sort is EventSort.start_asc else None
        names_only = rank is not None and free_text(q or "", cfg)

        def build(session: Session) -> EventPage:
            # Cards never show the long text columns.
            card = select(EventRow).options(
                selectinload(EventRow.images), defer(EventRow.description), defer(EventRow.extra)
            )
            # Paid matches lead page 1 and are left out of the rest, so every later page starts that
            # many places earlier: the total and the paging stay those of the plain list.
            paid = (
                ads.promoted_for(session, card, filters, page, min(cfg.api.sponsored_results, size))
                if sponsored
                else None
            )
            first, drop = paid if paid else ([], [])
            where = [*filters, EventRow.id.not_in(drop)] if drop else filters
            where = [*where, placecards.first_opening(where)]  # a place is one card, not one per day
            start, stop = max((page - 1) * size - len(drop), 0), page * size - len(drop)
            weak: set[str] = set()

            def by_date(where: list[ColumnElement[bool]]) -> tuple[int, Sequence[EventRow]]:
                total = session.scalar(select(func.count()).select_from(EventRow).where(*where)) or 0
                # By date: a day's events first, then its place listings (a club or café "open today",
                # thousands of them at 00:00, which would otherwise bury every real event).
                # Not abroad: there local_date is the venue's own day, while cards show Israel time.
                abroad = request.query_params.get("abroad") == "true"
                order = (
                    (EventRow.local_date.asc().nulls_first(), _IS_PLACE, _START)
                    if sort is EventSort.start_asc and not abroad
                    else (_SORT_COLUMNS[sort], _START)
                )
                if q and sort is EventSort.start_asc and not abroad:  # a search covers abroad too: Israel's first
                    order = (EventRow.country.is_not(None), *order)
                rows = session.scalars(
                    card.where(*where).order_by(*order, EventRow.id).offset(start).limit(stop - start)
                ).all()
                return total, rows

            def by_rank(
                where: list[ColumnElement[bool]], q: str, alike: list[str]
            ) -> tuple[int, Sequence[EventRow], set[str]]:
                # Ranking reads every match anyway, so one pass gives both the order and the total
                # (measured at 35k events: as fast as the count + page it replaces). Within a rank,
                # events in Israel before those abroad (the search covers both), then by date.
                # A search matching thousands ("מסיבה") is listed by date: ranking reads every match
                # (measured: ~0.1 ms a row), and its matches are mostly the same kind anyway.
                if (
                    session.scalar(
                        select(func.count()).select_from(
                            select(EventRow.id).where(*where).limit(RANK_MAX + 1).subquery()
                        )
                    )
                    or 0
                ) > RANK_MAX:
                    return (*by_date(where), set())
                cols = ranked_columns(*where)
                rank = relevance(q, cfg, alike, cols)
                assert rank is not None
                ranked = session.execute(
                    select(cols.c.id, rank).order_by(
                        rank, cols.c.country.is_not(None), cols.c.start_at.asc().nulls_first(), cols.c.id
                    )
                ).all()
                wanted = [i for i, _ in ranked[start:stop]]
                weak = {i for i, r in ranked[start:stop] if tier(r) == 2} if names_only else set()
                found = {r.id: r for r in session.scalars(card.where(EventRow.id.in_(wanted)))}
                return len(ranked), [found[i] for i in wanted if i in found], weak

            if rank is None:
                total, rows = by_date(where)
            else:
                total, rows, weak = by_rank(where, q or "", _sounds_like(request, q or ""))
            suggestion = widened = widened_q = None
            # Nothing found: each word no title or name has, spelled as the closest one that has ("omer adm").
            fixed = (
                correct_spelling(q, _name_index(request), cfg.search.typo_cutoff)
                if q and not total and not drop
                else None
            )
            if fixed:
                alike = _sounds_like(request, fixed)
                respelled = [*filters[:-1], *smart_filters(fixed, cfg, alike)]  # the search is the last filter
                respelled.append(placecards.first_opening(respelled))
                again = relevance(fixed, cfg, alike) if rank is not None else None
                total, rows, weak = (
                    by_rank(respelled, fixed, alike) if again is not None else (*by_date(respelled), set())
                )
                if total:
                    widened, widened_q = "spelling", fixed
            total += len(drop)
            promoted = {r.id: pick for r, pick in first}
            rows = [r for r, _ in first] + list(rows)
            if q and not total and not drop and smart_filters(q, cfg):  # the search is the last filter
                # As the bot does: without the date words ("היום"), then without the place, keeping the rest.
                text = understand.clean(q, MAX_TEXT_PARAM)
                named = understand.kinds(text, cfg, understand.today_in(cfg))
                for kind in ("date", "place"):
                    if named[kind] and (wider := understand.without(text, named[kind])):
                        total, rows = by_date([*filters[:-1], *smart_filters(wider, cfg)])
                        if total:
                            widened, widened_q = kind, wider
                            break
            if q and not total:  # what people look for and we lack; once per cached answer
                log.info("search miss: %r", q[:100])
                names = (n for names in _sound_index(request).values() for n in names)
                if (near := did_you_mean(q, cfg, names)) and session.scalar(
                    # the search is the last filter: the other filters stay
                    select(EventRow.id).where(*filters[:-1], *smart_filters(near, cfg)).limit(1)
                ):
                    suggestion = near
            extras = _summary_extras(session, rows, cfg)
            items = [
                EventSummaryOut.model_validate(r).model_copy(
                    update=extras[r.id] | {"sponsored": promoted.get(r.id), "in_text": r.id in weak}
                )
                for r in rows
            ]
            return EventPage(
                total=total,
                page=page,
                page_size=size,
                items=items,
                did_you_mean=suggestion,
                widened=widened,
                widened_q=widened_q,
            )

        response = _cached(request, build)
        # A visitor's search, counted once: the site's results grid (sponsored=true), not the side counts
        # ("without this filter: N") that ask with the same words; later pages and the admin's own neither.
        if q and page == 1 and sponsored and "authorization" not in request.headers:
            total = json.loads(bytes(response.body))["total"]
            background.add_task(searches.record, request.app.state.db, q, total)
        return response

    @app.get("/events/map", response_model=MapOut, tags=["events"])
    def map_events(
        request: Request,
        filters: list[ColumnElement[bool]] = event_filters_dep,
        compact: Annotated[
            bool, Query(description="Columns and lookup tables instead of one object per pin (map_columns)")
        ] = False,
    ) -> Response:
        """All matching events with coordinates at once (the list's filters, no paging)."""

        def read(session: Session) -> tuple[list[Any], int, dict[str, list[str]]]:
            # A Zoom talk has no place: never a pin, even with its organiser's address (counted as unplaced).
            located = and_(EventRow.latitude.is_not(None), EventRow.longitude.is_not(None), in_person())
            rows = session.execute(
                select(
                    EventRow.id, EventRow.slug, EventRow.code, EventRow.title, EventRow.start_at, EventRow.end_at, EventRow.time_known,
                    EventRow.time_zone, EventRow.local_date, EventRow.venue_name,
                    EventRow.latitude, EventRow.longitude, EventRow.source, EventRow.group_id,
                )
                .where(*filters, located, placecards.first_opening(filters))  # a place: one pin
                .order_by(_START, EventRow.id)
            ).all()  # fmt: skip
            unplaced = session.scalar(select(func.count()).select_from(EventRow).where(*filters, not_(located))) or 0
            return list(rows), unplaced, _group_sources(session, rows)  # type: ignore[arg-type]

        def build(session: Session) -> MapOut:
            rows, unplaced, sources = read(session)
            items = [MapEventOut.model_validate(r).model_copy(update={"sources": sources[r.id]}) for r in rows]
            return MapOut(items=items, unplaced=unplaced)

        if compact:
            cache: ResponseCache = request.app.state.response_cache

            def body() -> bytes:
                with request.app.state.db.session(write=False) as session:
                    return json.dumps(map_columns(*read(session)), ensure_ascii=False, separators=(",", ":")).encode()

            response = Response(
                cache.get((request.url.path, request.url.query), body)[0], media_type="application/json"
            )
        else:
            response = _cached(request, build)
        # The JSON's size before gzip: the map counts the bytes as they arrive (unzipped) for its real
        # percent; Content-Length is the zipped size, so it can't tell (MapPage, api.ts getJSONProgress).
        response.headers["X-Body-Length"] = str(len(response.body))
        return response

    @app.get("/go/{event_id:path}", include_in_schema=False)
    def go(
        event_id: str, request: Request, session: SessionDep, at: str | None = None,
        link: Annotated[int | None, Query(ge=0, le=100)] = None,
    ) -> RedirectResponse:  # fmt: skip
        """The seller's page of one listing: the only place a ticket link leaves the server
        (one click at a time, rate-limited by SecurityMiddleware), counted, and UTM-tagged for the
        seller's analytics. ``at`` = where on our page it was clicked (utm.PLACEMENTS). ``link`` = which
        of a posted event's ticket links (extra.ticket_links, partylinks.community); else its first."""
        code = shortlinks.normalize(event_id) if ":" not in event_id else None  # listing ids are "source:id"
        row = session.scalar(select(EventRow).where(EventRow.code == code)) if code else session.get(EventRow, event_id)
        headers = {"Cache-Control": "private, no-store", "X-Robots-Tag": "noindex, nofollow"}
        if not row or not row.url.startswith(("https://", "http://")):
            # An event that is over is deleted (no past events kept): 410, so search engines drop the link.
            raise HTTPException(status.HTTP_410_GONE, "event gone", headers=headers)
        if not CRAWLER.search(request.headers.get("user-agent", "")):  # Googlebot checks offers.url: not a click
            clicks.record(session, row, at)
        target = row.url
        links = (row.extra or {}).get("ticket_links") or []
        if link is not None and link < len(links) and str(links[link].get("url", "")).startswith("https://"):
            target = links[link]["url"]
        url = utm.tagged(target, request.app.state.settings.utm, row, at)
        return RedirectResponse(url, status.HTTP_302_FOUND, headers=headers)

    # Before /events/{event_ref}, which would otherwise take "sponsored" for a slug.
    from partylinks.ads.routes import admin_router as ads_admin_router
    from partylinks.ads.routes import router as ads_router

    app.include_router(ads_router)
    from partylinks.plans.routes import router as plans_router

    app.include_router(plans_router)
    from partylinks.placecards.routes import router as places_router

    app.include_router(places_router)
    from partylinks.placeinfo.routes import router as place_info_router

    app.include_router(place_info_router)
    from partylinks.artist_filter.routes import router as artist_filter_router
    from partylinks.evening.routes import router as evening_router
    from partylinks.holiday_pages.routes import router as holidays_router
    from partylinks.similar.routes import router as similar_router
    from partylinks.weekly_top.routes import router as top_router

    app.include_router(evening_router)
    app.include_router(holidays_router)
    app.include_router(similar_router)
    app.include_router(artist_filter_router)
    from partylinks.facet_list.routes import router as facet_list_router

    app.include_router(facet_list_router)  # the venues / artists indexes, a page at a time
    app.include_router(top_router)
    app.include_router(ads_admin_router)
    from partylinks.home_pins.routes import router as home_pins_router

    app.include_router(home_pins_router)
    from partylinks.promote.routes import admin as promote_admin_router
    from partylinks.promote.routes import public as promote_router

    app.include_router(promote_router)  # the /promote form
    app.include_router(promote_admin_router)
    from partylinks.community.routes import admin as community_admin_router
    from partylinks.community.routes import public as community_router

    app.include_router(community_router)  # the /submit form: events users post
    app.include_router(community_admin_router)
    from partylinks.contact.routes import admin as contact_admin_router
    from partylinks.contact.routes import router as contact_router

    app.include_router(contact_router)  # the /contact form
    app.include_router(contact_admin_router)
    from partylinks.config_store.routes import router as config_overrides_router

    app.include_router(config_overrides_router)

    @app.get("/events/rails", response_model=RailsOut, tags=["events"])
    def rails(request: Request) -> Response:
        """Every home-page row (settings home_rails) in one cached response, warmed on startup: the busiest
        events of each, from tonight to the row's horizon."""
        cfg: Settings = request.app.state.settings

        def build(session: Session) -> RailsOut:
            now = datetime.now(ZoneInfo(cfg.timezone))
            out = RailsOut()

            # "latest" rows: the latest to end first (no end time: the start plus the assumed length, bound as an
            # interval: a bare timedelta reached Postgres as a timestamp and the row failed).
            length = literal(timedelta(hours=cfg.retention.assumed_duration_hours), Interval())
            ends = func.coalesce(EventRow.end_at, EventRow.start_at + length)
            orders = {
                "trending": (_SORT_COLUMNS[EventSort.trending], _START),
                "latest": (ends.desc().nulls_last(), _START),
                "rating": (EventRow.extra["rating"].as_float().desc().nulls_last(), _START),
            }

            def load(filters: list[ColumnElement[bool]], pool: int, sort: str = "trending") -> list[EventSummaryOut]:
                where = [*filters, placecards.first_opening(filters)]  # a place is one card
                rows = session.scalars(
                    select(EventRow)
                    .where(*where, nearest_show(where))  # and so is a show, whatever its dates
                    .order_by(*orders[sort], EventRow.id)
                    .limit(pool)
                    .options(selectinload(EventRow.images), defer(EventRow.description), defer(EventRow.extra))
                ).all()
                extras = _summary_extras(session, rows, cfg)
                return [EventSummaryOut.model_validate(r).model_copy(update=extras[r.id]) for r in rows]

            # Rows only we can make (open now, price dropped, the weather...): after smart_rails.after.
            city = next((c for c in cfg.enrichment.cities if c.key == cfg.smart_rails.weather_city and c.center), None)
            slots = cached_slots(cfg, *city.center) if city and city.center else []
            smart = smartrails.rail_filters(cfg, now, lambda **kw: event_filters(request, **kw), slots)
            # The coming holiday's row, from rail_days before it (partylinks.holiday_pages).
            soon = now.date() + timedelta(days=cfg.holiday_pages.rail_days)
            coming = next(
                ((h, n) for h, n in holiday_pages.with_pages(session, cfg, now.date()) if h.start <= soon), None
            )
            if coming:
                holiday, n = coming
                out.holiday = HolidayOut(key=holiday.key, he=holiday.he, en=holiday.en, start=holiday.start, end=holiday.end,
                                         events=n, page=True)  # fmt: skip
                smart = {"holiday": [*event_filters(request, date_to=now + timedelta(days=cfg.holiday_pages.ahead_days)),
                                     *holiday_pages.where(holiday)], **smart}  # fmt: skip
            wheres = dict(smart)  # each row's filters, for the pinned events (partylinks.home_pins)
            specs = [r for r in cfg.home_rails.rows if not (r.holiday and out.holiday and out.holiday.key == r.holiday)]
            for spec in specs:  # the coming holiday's own row already shows its row's events
                try:
                    wheres[spec.key] = _home_row_filters(lambda **kw: event_filters(request, **kw), cfg, spec, now)
                except Exception:  # noqa: BLE001 - an admin's bad row must not take the home page down
                    broken_home_rail(spec.key)
            tags = {r.key: r.cache_tag(now.date()) for r in specs if r.key in wheres}
            rows_cache: RowCache = request.app.state.home_rows
            if rows_cache.empty() and (old := request.app.state.response_cache.body((request.url.path, ""))):
                with contextlib.suppress(ValueError, KeyError, TypeError):  # an older response: rows to start from
                    kept = json.loads(old)["rails"]
                    rows_cache.seed(  # without its pins: home_pins adds the live ones on top
                        {
                            k: [EventSummaryOut.model_validate(c) for c in v if not c.get("pinned")]
                            for k, v in kept.items()
                        },
                        tags,
                    )

            sorts = {r.key: r.sort for r in specs}

            def build_row(key: str) -> list[EventSummaryOut]:
                with session.begin_nested():  # a failed query rolls back to here, the other rows still load
                    return load(wheres[key], cfg.home_rails.pool, sorts.get(key, "trending"))

            rows = rows_cache.rows(tags, build_row, cfg.home_rails.row_ttl_seconds, cfg.home_rails.rows_per_build)

            def add_smart() -> None:
                out.rails.update({k: load(f, cfg.smart_rails.pool) for k, f in smart.items()})

            if cfg.smart_rails.after not in rows:  # no such row (or it failed): they lead
                add_smart()
            for key, cards in rows.items():
                out.rails[key] = cards
                if key == cfg.smart_rails.after:
                    add_smart()
            home_pins.pin(session, out.rails, wheres, event_filters(request), load)
            return out

        return _cached(request, build)

    @app.get("/events/{event_ref}", response_model=EventOut, tags=["events"])
    def get_event(event_ref: str, session: SessionDep, request: Request) -> EventOut:
        """Look an event up by id (``source:source_event_id``), short code or slug. Opening it counts a view."""
        row = session.scalar(
            select(EventRow)
            .where(or_(EventRow.id == event_ref, EventRow.slug == event_ref, EventRow.code == event_ref))
            .options(selectinload(EventRow.tickets), selectinload(EventRow.images))
        )
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found")
        cfg: Settings = request.app.state.settings
        row = primary_listing(session, row)  # one page per real event: its group's primary listing
        if who := popularity.visitor(
            request.client.host if request.client else None, request.headers.get("user-agent", "")
        ):
            popularity.record_later(request.app.state.db, row.id, who, cfg.timezone)
        alternatives = (
            session.scalars(
                select(EventRow)
                .where(EventRow.group_id == row.group_id, EventRow.id != row.id)
                .order_by(EventRow.min_price.asc().nulls_first(), EventRow.id)
                .options(selectinload(EventRow.tickets))
            ).all()
            if row.group_id
            else []
        )
        text = group_description(session, row, cfg.event_text)  # empty here: the same event's text on another site
        listing_ids = [row.id, *(a.id for a in alternatives)]  # changes of every listing of the event
        changes = session.scalars(
            select(DealChangeRow)
            .where(DealChangeRow.event_id.in_(listing_ids))
            .order_by(DealChangeRow.at.desc(), DealChangeRow.id.desc())
            .limit(cfg.api.event_changes_limit)
        ).all()
        return EventOut.model_validate(row).model_copy(
            update={
                "alternatives": [
                    AlternativeOut.model_validate(a).model_copy(
                        update={
                            "resale": bool((a.extra or {}).get("resale")),
                            # viagogo rows its feed dropped are never re-read (unlist_missing off): no seller then, none now.
                            "is_sold_out": a.is_sold_out or (a.extra or {}).get("has_listings") is False,
                        }
                    )
                    for a in alternatives
                ],
                "changes": [DealChangeOut.model_validate(c) for c in changes],
                **_summary_extras(session, [row])[row.id],
                "images": [ImageOut.model_validate(i) for i in live_images(row)],
                "watching": popularity.watching(session, row.id, cfg.popularity),
                "lineup_links": artist_links_for(session, cfg.artist_links, row.lineup or []),
                "lineup_images": lineup_images(session, row.lineup or []),
                "description": text,
                "description_layout": description_layout(text, cfg.event_text),
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

    @app.get("/venue-logos/{key}.webp", tags=["events"], response_class=Response)
    def venue_logo(key: str, request: Request) -> Response:
        found = request.app.state.service.venues.logo(key)
        if found is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no logo")
        data, updated = found
        etag = f'"{key}-{int(updated.timestamp())}"'
        headers = {"Cache-Control": "public, max-age=86400", "ETag": etag}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
        return Response(content=data, media_type="image/webp", headers=headers)

    @app.get("/images/{image_id}", tags=["events"], response_class=Response)
    def get_image(image_id: int, request: Request, session: ReleasedSessionDep) -> Response:
        return _image_response(image_id, request, session)

    @app.get("/images/{image_id}/{width}.webp", tags=["events"], response_class=Response)
    def get_image_copy(image_id: int, width: int, request: Request, session: ReleasedSessionDep) -> Response:
        """The artwork resized to ``width`` (640 cards, 960 event page; 1280, the old size, serves the 960) as WebP. A copy not made
        yet (or never: animations, unreadable files) serves the original, cached for an hour only,
        so the edge picks up the copy once it exists. Never converts on request."""
        if width in LEGACY_WIDTHS:
            width = VARIANT_WIDTHS[-1]
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
        # An id's picture never changes (a new URL is a new row): the edge keeps it for good (blog covers,
        # og:image, JSON-LD all point at the full size).
        headers = {"Cache-Control": "public, max-age=31536000, immutable", "ETag": etag}
        if width is not None:
            copy = request.app.state.service.media.copy_path(meta.local_path, width) if meta.local_path else None
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
        if image.local_path and not path.is_file():  # converted: the original is gone, its big copy stands in
            path, media_type = media.big_copy(image.local_path), "image/webp"
        if not image.local_path or not path.is_file():
            raise HTTPException(status.HTTP_404_NOT_FOUND, "image file missing")
        return FileResponse(path, media_type=media_type, headers=headers)

    @app.get("/categories", response_model=Categories, response_model_exclude_none=True, tags=["events"])
    def categories(
        request: Request,
        filters: list[ColumnElement[bool]] = event_filters_dep,
        upcoming: bool = True,
        lists: Annotated[
            bool,
            Query(
                description="With the venue and artist lists (false: the filter's counts only, a far smaller answer)"
            ),
        ] = True,
    ) -> Response:
        """Facet counts of the events matching the list's filters (none: every upcoming event)."""
        cache: ResponseCache = request.app.state.response_cache

        def body() -> bytes:  # not _cached: build_categories opens its own short session
            built = build_categories(request, filters, upcoming, lists)
            # A filter needs no venue / artist names: abroad's were 4,567 + 1,709 of its 782 KB (Germany, 25.11.2026),
            # handed to every visitor of the page, a copy of the whole index in one call.
            drop = None if lists else {"venues", "lineup"}
            return built.model_dump_json(by_alias=True, exclude_none=True, exclude=drop).encode()

        return Response(cache.get((request.url.path, request.url.query), body)[0], media_type="application/json")

    def anywhere_categories() -> bytes:
        """/categories?anywhere=true's JSON (the venue and artist indexes page it, partylinks.facet_list)."""
        built = build_categories(SimpleNamespace(app=app), build_event_filters(app.state.settings, anywhere=True), True)  # type: ignore[arg-type]
        return built.model_dump_json(by_alias=True, exclude_none=True).encode()

    app.state.anywhere_categories = anywhere_categories

    def suggest_index() -> SuggestIndex:
        """The artist and venue lists of /categories?anywhere=true (the response cache's copy, rebuilt with it)."""
        body, _ = app.state.response_cache.get(("/categories", "anywhere=true"), anywhere_categories)
        built = getattr(app.state, "suggest_index", None)
        if built is None or built[0] is not body:
            lists = json.loads(body)
            built = (body, SuggestIndex({"artists": lists.get("lineup", []), "venues": lists.get("venues", [])}))
            app.state.suggest_index = built
        return built[1]

    @app.get("/search/suggest", tags=["events"])
    def search_suggest(
        q: Annotated[str, Query(max_length=MAX_TEXT_PARAM, description="What was typed so far")],
        limit: Annotated[int, Query(ge=1, le=10)] = 5,
    ) -> Response:
        """Artists and venues whose names match what was typed in the search box (prefix, any word's start,
        inside, or the same name in the other alphabet), busiest first: the box's suggestions, at every
        keystroke, instead of the full name index (~450 KB) in the browser."""
        found = suggest_index().find(q, limit)
        keep = ("value", "events", "verified", "image_url", "area")
        body = {kind: [{k: x[k] for k in keep if x.get(k) is not None} for x in items] for kind, items in found.items()}
        return JSONResponse(body, headers={"Cache-Control": "public, max-age=60"})

    @app.get("/ticket-sites", response_model=Stores, tags=["events"])
    def stores(request: Request) -> Response:
        """The ticket sites events come from, with how many upcoming events each sells (the stores page)."""

        def build(session: Session) -> Stores:
            where = [upcoming_filter()]
            listings = session.execute(
                select(EventRow.group_id, EventRow.source).where(*where, placecards.first_opening(where)).distinct()
            ).all()  # a place once, not once per day it opens: the same count as its "their events" list
            counts = Counter(source for _, source in listings)
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
            stores = [StoreOut(name=s, events=n, updated_at=done.get(s)) for s, n in counts.most_common()]
            sources = configured_sources(request.app.state.settings)
            offices = box_offices(session, sources)
            return Stores(stores=stores, total=len({group for group, _ in listings}), box_offices=offices)

        return _cached(request, build)

    @app.get("/locate", tags=["events"])
    def locate(
        request: Request,
        lat: Annotated[float, Query(ge=-90, le=90)],
        lng: Annotated[float, Query(ge=-180, le=180)],
    ) -> dict[str, str | None]:
        """Region (and canonical city, when inside one) of a point: "near me" on the home page. Outside Israel:
        ``country`` and ``abroad_city``, the nearest city there ("near me" on /abroad)."""
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
        found: dict[str, str | None] = {"region": region if known else None, "city": city.he if city else None}
        if region == ABROAD_AREA and (near := _nearest_abroad(request, here)):
            found |= {"country": near[0], "abroad_city": near[1]}
        return found

    def _nearest_abroad(request: Request, here: tuple[float, float]) -> tuple[str, str] | None:
        """(country, city) of the city abroad with coming events nearest to ``here``, within 150 km: the
        city as our events name it (the /abroad page filters by it), placed at its events' mean point."""
        state = request.app.state
        built, cities = getattr(state, "abroad_city_points", (0.0, None))
        if cities is None or time.monotonic() - built > 600:  # rebuilt every 10 minutes (one grouped read)
            with state.db.session(write=False) as session:
                cities = session.execute(
                    select(EventRow.country, EventRow.city, func.avg(EventRow.latitude), func.avg(EventRow.longitude))
                    .where(EventRow.country.is_not(None), EventRow.city.is_not(None), EventRow.latitude.is_not(None),
                           EventRow.is_primary.is_(True), upcoming_filter())
                    .group_by(EventRow.country, EventRow.city)
                ).all()  # fmt: skip
            state.abroad_city_points = (time.monotonic(), cities)
        best = min(((meters(here, (lat, lng)), country, city) for country, city, lat, lng in cities), default=None)
        if not best or best[0] > 150_000:
            return None
        # The name the city box shows: "Ludwigshafen am Rhein" (2 events) is listed as "Ludwigshafen" (107).
        short = _short_cities((city, country) for country, city, _, _ in cities)
        return best[1], short.get((best[2], best[1]), best[2])

    def build_categories(
        request: Request, filters: list[ColumnElement[bool]], upcoming: bool, lists: bool = True
    ) -> Categories:
        """``lists=False``: no venue and artist lists, not even counted (the filter's counts only: a country's
        cities on /abroad were 3-9 s cold, most of it the name lists thrown away after, 25.11.2026)."""
        # Only the facet columns (not descriptions/extra JSON); each real event counted once.
        lineup = EventRow.lineup if lists else null().label("lineup")  # the line-ups' JSON: only for the list
        query = select(
            EventRow.genres, EventRow.local_date, EventRow.day_of_week, EventRow.time_slot, EventRow.area,
            EventRow.group_entry_type, EventRow.venue_name, EventRow.city, lineup, EventRow.event_types,
            EventRow.source, EventRow.in_target_city, EventRow.country, EventRow.latitude, EventRow.longitude,
        ).where(*filters)  # fmt: skip
        # Per ticket site: real events it sells (every listing of a group counts, once per group), within the
        # request's filters: an artist's sites on its page, abroad sites abroad, only Israel's on the home page.
        matching = select(EventRow.group_id).where(*filters)
        where = [EventRow.group_id.in_(matching), *([upcoming_filter()] if upcoming else [])]
        listings = select(EventRow.group_id, EventRow.source).where(*where, placecards.first_opening(where)).distinct()
        # Every read in one short transaction, then the counting with none open: a read transaction held over
        # the counting (minutes for every event, anywhere=true, on a busy server) was killed by
        # idle_in_transaction_session_timeout, and the page answered 500 (2026-11-22, every few minutes).
        db: Database = request.app.state.db
        with db.session(write=False) as session:
            rows = session.execute(query).all()
            listed = session.execute(listings).all()
            venue_profiles, linked = (
                (cached_venue_profiles(session), linked_venue_names(session)) if lists else ([], {})
            )
        group_sources = Counter(source for _, source in listed)

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
                elif not request.app.state.settings.site.artists_verified_only:
                    out.append(FacetValue(value=name, events=n))
            return out

        region_keys = request.app.state.service.enricher.areas.region_keys

        def region(r: Any) -> str | None:
            return "tel_aviv" if r.in_target_city else r.area if r.area in region_keys else None

        # The same regions as the home filter (not Tel Aviv neighbourhoods mixed with regions).
        venue_area = {r.venue_name: reg for r in rows if r.venue_name and (reg := region(r))}

        city_regions = Counter((r.city, reg) for r in rows if r.city and (reg := region(r)))
        city_region: dict[str, str] = {}
        for (city, reg), _ in city_regions.most_common():
            city_region.setdefault(city, reg)  # a city's most common region
        enrichment = request.app.state.settings.enrichment
        genres = facet(lambda r: enrichment.with_parents(r.genres or []))
        counts = {g.value: g.events for g in genres}
        return Categories(
            genres=genres,
            genre_groups=[
                FacetValue(value=g, events=counts[g], members=members)
                for g, members in enrichment.genre_groups.items()
                if g in counts
            ],
            genre_families=enrichment.genre_families,
            dates=sorted(facet(lambda r: [r.local_date]), key=lambda f: f.value),
            days_of_week=facet(lambda r: [r.day_of_week]),
            time_slots=facet(lambda r: [r.time_slot]),
            areas=facet(lambda r: [r.area]),
            regions=facet(lambda r: [region(r)]),
            entry_types=facet(lambda r: [r.group_entry_type]),
            venues=venue_facets(facet(lambda r: [r.venue_name]), venue_area, rows, venue_profiles, linked)
            if lists
            else [],
            cities=[FacetValue(value=c, events=n, area=city_region.get(c)) for c, n in _merged_cities(rows)],
            countries=facet(lambda r: [r.country]),
            lineup=artist_facet() if lists else [],
            event_types=facet(lambda r: r.event_types or []),
            sources=[FacetValue(value=s, events=n) for s, n in group_sources.most_common()],
        )

    @app.get("/sources", response_model=list[SourceOut], tags=["sources"], dependencies=[AdminDep])
    def list_sources(request: Request, session: SessionDep) -> list[SourceOut]:
        service: ScrapeService = request.app.state.service
        worker = request.app.state.worker
        next_runs = worker.next_runs() if worker else next_run_times(request.app.state.scheduler)
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
        """Purge now (in this process: retention is a few bulk deletes, not a scrape)."""
        try:
            return RetentionOut(removed_events=await request.app.state.service.purge_expired())
        except SourceBusyError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    @app.get("/maintenance/retention", tags=["maintenance"], dependencies=[AdminDep])
    def retention_status(request: Request) -> dict[str, Any]:
        cfg: Settings = request.app.state.settings
        if worker := request.app.state.worker:  # the scheduled passes run in the worker
            return {**cfg.retention.model_dump(), "next_run_at": worker.next_runs().get(RETENTION_JOB_ID),
                    **((worker.state() or {}).get("retention") or {})}  # fmt: skip
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
