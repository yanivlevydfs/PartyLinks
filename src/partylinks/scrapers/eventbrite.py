"""Eventbrite (eventbrite.com) - concerts, club nights, festivals, stage, film and food events worldwide, and all of Israel.

- **Discovery.** ``/d/<place>/<listing>/?page=N`` renders its results in ``window.__SERVER_DATA__``: 20
  a page, at most 49 pages a listing (980 events), ranked by relevance (not by date). Each entry: name, local start /
  end with the venue's zone, the venue (id, name, address, coordinates), the category, subcategory and
  format (``tags``), artwork and the online / protected / cancelled flags. ``<listing>`` is
  ``all-events`` or one category / format (``music--events``, ``parties``...: ``listings``).
- **Israel** (``israel_paths``): the Israel page and the Israeli city searches, every category, every run,
  each to its last page (~50 listed events, Nov 2026; 20 more city pages checked list the same ones). A
  city search covers a radius, so Jordanian and Lebanese venues are dropped.
- **Every date in Israel.** A search shows a series once (its next date, ``num_children`` dates in all)
  and one event of a group of same-name events (``dedup.count``: a weekly evening published one event a
  date). The series' coming dates come from ``/api/v3/series/<id>/events/`` (one call, each date's id,
  link, times and place; the dates share the listed date's page), the group's from its organizer's live
  events (``/api/v3/organizers/<id>/events/``, 50 a page: those of the same name at an Israeli place,
  each read like a listed one). ~50 listed events give ~700 dates (Nov 2026). Abroad the same, up to
  the city's next read (``cycle_runs`` x ``rotation_hours`` ahead, a slot more): a later date is read
  by a later visit, when it comes that close (a New York listing's 20 events have ~500 dates, most of
  them months away).
- **Abroad** (``cities``, Eventbrite's place slugs of the cities SPOTX lists abroad): city ``i`` is read in
  rotation slot ``i % cycle_runs`` (one slot per ``rotation_hours``), so the whole list is read once a
  cycle. A city's all-events page 1 gives its size: one of up to ``small_city_pages`` pages is read whole
  and kept by category (``relevant_tags``); a bigger one is read through ``listings`` instead, each to
  its last page. Business, tech, health and other categories are left out abroad.
- **Past 980.** A listing with more events than its 49 pages show (New York's music: 3,420, Nov 2026) is
  read by date windows (``start_date`` / ``end_date``, both days included), each halved until it fits,
  from yesterday to ``window_days`` ahead.
- **Flaky search.** Many discovery pages (about half, Nov 2026) come back after ~11 s with no results and
  no pagination: the search behind them timed out. Such a page is asked again, ``empty_retries`` times.
- **Event page** (``/e/...``, Next.js ``__NEXT_DATA__``, ~200 KB, ~0.5 s): description, price range
  (schema.org offers), sale status, organizer, the lineup (headliners and supporters; name, picture and
  links of each artist) and the category / subcategory / format names. Read for every event the run
  lists: Israel first, then each city in turn, soonest first (a run cut short keeps the places it read;
  an event two nearby cities list is read once).
- **Venues.** Every venue of the run becomes a venue profile (name, address, pin); one whose events are
  organised by the venue itself (same name) also takes the organizer's about and page.

Online, cancelled, password-protected and past events are skipped. robots.txt allows ``/d/``, ``/e/``,
``/api/v3/series/`` and ``/api/v3/organizers/`` (it disallows the ``/api/v3/destination/events/`` API,
which is not used).
"""

from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from pydantic import Field

from partylinks.artists import ArtistProfile
from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import (
    ParseError,
    clean_str,
    html_to_text,
    jsonld_nodes,
    next_data,
    parse_iso,
    schema_types,
    to_decimal,
    to_float,
)
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.venue_profiles import VENUE_PROFILE, VenueProfile

_MARK = "window.__SERVER_DATA__ = "
MOST_PAGES, PAGE_SIZE = 49, 20  # a discovery listing shows at most 49 pages of 20
_AGE = re.compile(r"(\d{1,2})\s*\+")


class EventbriteSettings(SourceSettings):
    site_url: str = "https://www.eventbrite.com"
    # Read whole every run, every category: the Israel page, then the city searches (each lists events it misses).
    israel_paths: list[str] = Field(
        default_factory=lambda: [
            "/d/israel/all-events/",
            "/d/israel--tel-aviv/all-events/",
            "/d/israel--jerusalem/all-events/",
            "/d/israel--haifa/all-events/",
            "/d/israel--beersheba/all-events/",
            "/d/israel--netanya/all-events/",
            "/d/israel--eilat/all-events/",
        ]
    )
    # Venue countries kept from the Israeli searches: their radius reaches Jordan and Lebanon.
    israel_countries: list[str] = Field(default_factory=lambda: ["IL"])
    # Every date of a listed event: its series' dates, and the organizer's same-name events (a dedup group).
    series_path: str = "/api/v3/series/{id}/events/"
    organizer_path: str = "/api/v3/organizers/{id}/events/"
    series_page_size: int = Field(200, ge=1, le=200)
    api_live_statuses: list[str] = Field(default_factory=lambda: ["live", "started"])
    every_date_abroad: bool = True  # abroad too, up to the city's next read (False: the listed date only)
    # Eventbrite's place slugs abroad ("germany--berlin", "ny--new-york"), busiest first: read in rotation.
    cities: list[str] = Field(default_factory=list)
    cycle_runs: int = Field(28, ge=1)
    rotation_hours: int = Field(12, ge=1)
    # A city whose all-events listing has at most this many pages is read whole and kept by category.
    small_city_pages: int = Field(8, ge=1)
    # A bigger city's listings: the path after /d/<place>/ -> the tag it lists.
    listings: dict[str, str] = Field(
        default_factory=lambda: {
            "music--events": "EventbriteCategory/103",
            "arts--events": "EventbriteCategory/105",
            "film-and-media--events": "EventbriteCategory/104",
            "food-and-drink--events": "EventbriteCategory/110",
            "holiday--events": "EventbriteCategory/116",
            "parties": "EventbriteFormat/11",
            "festivals": "EventbriteFormat/5",
            "performances": "EventbriteFormat/6",  # concerts and shows of any category (2,283 in New York)
        }
    )
    # Abroad, an event is kept when it carries one of these: the listings' tags, and concerts / shows.
    relevant_tags: list[str] = Field(
        default_factory=lambda: [
            "EventbriteCategory/103", "EventbriteCategory/104", "EventbriteCategory/105", "EventbriteCategory/110",
            "EventbriteCategory/116", "EventbriteFormat/5", "EventbriteFormat/6", "EventbriteFormat/11",
        ]
    )  # fmt: skip
    # A listing past its 49 pages is read by date windows, from yesterday up to this many days ahead
    # (New York's music: none past 5 months, Nov 2026).
    window_days: int = Field(1100, ge=1)
    empty_retries: int = Field(3, ge=0)
    empty_retry_seconds: float = Field(5.0, ge=0)
    # The event page's status values of an event that will not take place.
    skip_statuses: list[str] = Field(default_factory=lambda: ["canceled", "cancelled", "postponed", "draft", "deleted"])
    # Eventbrite's categories and formats (tag ids) as category words the enrichment knows (its own
    # names, "Performing & Visual Arts", match nothing). A category counts only when its subcategory names
    # no genre: "Comedy" under Film & Media is stand-up, not cinema.
    tag_genres: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "EventbriteCategory/101": ["meetups"],         # Business & Professional
            "EventbriteCategory/102": ["technology"],      # Science & Technology -> hi-tech
            "EventbriteCategory/103": ["music"],           # Music -> live shows (not a party's)
            "EventbriteCategory/104": ["film"],            # Film, Media & Entertainment -> cinema
            "EventbriteCategory/105": ["culture"],         # Performing & Visual Arts
            "EventbriteCategory/106": ["culture"],         # Fashion & Beauty (shows)
            "EventbriteCategory/107": ["wellness"],        # Health & Wellness
            "EventbriteCategory/108": ["sports"],          # Sports & Fitness
            "EventbriteCategory/109": ["tours"],           # Travel & Outdoor
            "EventbriteCategory/110": ["food"],            # Food & Drink
            "EventbriteCategory/111": ["volunteering"],    # Charity & Causes
            "EventbriteCategory/112": ["lecture"],         # Government & Politics
            "EventbriteCategory/113": ["community"],       # Community & Culture -> meetups
            "EventbriteCategory/114": ["religious"],       # Religion & Spirituality
            "EventbriteCategory/115": ["family"],          # Family & Education -> kids
            "EventbriteCategory/116": ["holidays"],        # Seasonal & Holiday
            "EventbriteCategory/117": ["workshop"],        # Home & Lifestyle
            "EventbriteCategory/118": ["attractions"],     # Auto, Boat & Air (shows)
            "EventbriteCategory/119": ["workshop"],        # Hobbies & Special Interest
            "EventbriteCategory/120": ["family"],          # School Activities
            "EventbriteFormat/1": ["conference"],          # Conference
            "EventbriteFormat/2": ["lecture"],             # Seminar or Talk
            "EventbriteFormat/3": ["expo"],                # Tradeshow, Consumer Show, or Expo
            "EventbriteFormat/4": ["conference"],          # Convention
            "EventbriteFormat/5": ["festival"],            # Festival or Fair
            "EventbriteFormat/7": ["screening"],           # Screening
            "EventbriteFormat/9": ["workshop"],            # Class, Training, or Workshop
            "EventbriteFormat/10": ["meetups"],            # Meeting or Networking Event
            "EventbriteFormat/11": ["party"],              # Party or Social Gathering -> club nights
            "EventbriteFormat/13": ["sports"],             # Tournament
            "EventbriteFormat/15": ["sports"],             # Race or Endurance Event
            "EventbriteFormat/16": ["tours"],              # Tour
            "EventbriteFormat/17": ["attractions"],        # Attraction
        }
    )  # fmt: skip
    # A party's music category is no live show: these categories' words are dropped beside these formats.
    party_formats: list[str] = Field(default_factory=lambda: ["EventbriteFormat/11"])
    party_drops: list[str] = Field(default_factory=lambda: ["EventbriteCategory/103"])
    # Subcategories (their name, lower case) as category words; unlisted ones ("Other") leave the category's.
    subcategory_genres: dict[str, list[str]] = Field(
        default_factory=lambda: {
            # Music
            "alternative": ["alternative"], "blues & jazz": ["blues", "jazz"], "classical": ["classical"],
            "country": ["country music"], "edm / electronic": ["electronic"], "folk": ["folk"],
            "hip hop / rap": ["hip hop"], "indie": ["indie"], "latin": ["latin"], "metal": ["metal"],
            "opera": ["opera"], "pop": ["pop"], "r&b": ["r&b"], "reggae": ["reggae"], "rock": ["rock"],
            "top 40": ["top 40"], "americana": ["country music"], "bluegrass": ["bluegrass"], "blues": ["blues"],
            "dj/dance": ["dj set"], "edm": ["edm"], "electronic": ["electronic"], "jazz": ["jazz"],
            "psychedelic": ["rock"], "punk/hardcore": ["punk"], "world": ["world music"], "k-pop": ["k-pop"],
            "singer/songwriter": ["concert"], "acoustic": ["concert"], "experimental": ["concert"],
            # Performing & Visual Arts
            "theatre": ["theatre"], "musical": ["musical"], "ballet": ["ballet"], "dance": ["dance"],
            "orchestra": ["classical music"], "fine art": ["exhibition"], "sculpture": ["exhibition"],
            "literary arts": ["literature"], "comedy": ["comedy"],
            # Film, Media & Entertainment
            "film": ["film"], "gaming": ["esports"],
            # Food & Drink
            "beer": ["beer tasting"], "wine": ["wine tasting"], "spirits": ["whisky tasting"], "food": ["food"],
            # Community, Health
            "lgbt": ["lgbt"], "yoga": ["yoga"],
        }
    )  # fmt: skip


@dataclass
class _Venue:
    """A venue of this run's events: what the listing says, and its organizer when that is the venue."""

    name: str
    address: str | None
    latitude: float | None
    longitude: float | None
    profile_url: str | None
    about: str | None = None
    organizer_url: str | None = None


@dataclass
class RunStats:
    cities: list[str] = field(default_factory=list)
    pages: int = 0  # discovery pages with results
    empty: int = 0  # discovery pages still empty after the retries
    listed: int = 0
    dates: int = 0  # other dates of listed events: series dates and same-name events
    events: int = 0


class EventbriteScraper(BaseScraper[EventbriteSettings]):
    name = "eventbrite"
    settings_model = EventbriteSettings

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.stats = RunStats()
        self._venues: dict[str, _Venue] = {}
        self._seen: set[str] = set()  # event ids of this run (listed, or a listed event's other date)
        self._series: set[str] = set()  # series whose dates were asked for

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s, now = self.settings, datetime.now(UTC)
        due = cities_due(s.cities, now, s.rotation_hours, s.cycle_runs)
        self.stats.cities = due
        self.log.info(
            "rotation slot %d of %d: %s",
            rotation_slot(now, s.rotation_hours, s.cycle_runs),
            s.cycle_runs,
            ", ".join(due) or "-",
        )
        # Israel, then one city after the other, each listed and then read soonest first: a run cut short
        # (a deploy) has saved the places it got to. An event listed by two nearby cities is read once.
        for place in ["israel", *due]:
            events: dict[str, dict[str, Any]] = {}
            if place == "israel":
                await self._israel(events)
            else:
                await self.optional(f"city {place}", self._city(place, events))
            listed = [e for e in soonest_first(events.values()) if str(e["id"]) not in self._seen]
            self._seen.update(str(e["id"]) for e in listed)
            self.stats.listed += len(listed)
            self.log.debug("%s: %d new events listed", place, len(listed))
            async for event in self._read(listed):
                yield event
        self.log.info(
            "%d events listed, %d other dates, %d read (%d discovery pages, %d still empty after retries), %d venues",
            self.stats.listed, self.stats.dates, self.stats.events, self.stats.pages, self.stats.empty,
            len(self._venues),
        )  # fmt: skip
        self.venue_profiles = [venue_profile(key, v) for key, v in self._venues.items()]

    async def _read(self, listed: list[dict[str, Any]]) -> AsyncIterator[ScrapedEvent]:
        retention = RetentionSettings()
        async for found in self.stream_isolated(listed, self._event, label=lambda e: e.get("url", "")):
            for event in found:
                if is_current(event.start_at, event.end_at, retention, self.timezone, time_known=event.time_known):
                    self.stats.events += 1
                    yield event

    def _every_date(self, e: dict[str, Any]) -> bool:
        """Every date of this event is read: one in Israel (abroad with ``every_date_abroad``)."""
        return self.settings.every_date_abroad or self._in_israel(e)

    # ------------------------------------------------------------------ discovery
    async def _israel(self, events: dict[str, dict[str, Any]]) -> None:
        s = self.settings
        first, *rest = list(dict.fromkeys(s.israel_paths)) or [""]
        if first:  # the Israel page must answer
            await self._listing(first, events, keep=self._in_israel, must_answer=True)
        for path in rest:
            await self.optional(f"listing {path}", self._listing(path, events, keep=self._in_israel))
        await self.optional("same-name events", self._same_name(events, keep=self._in_israel))

    def _date_due(self, e: dict[str, Any]) -> bool:
        """An other date of an event to read now: any in Israel, abroad one before the city's next read."""
        if self._in_israel(e):
            return True
        s, start = self.settings, _when(e, "start")
        ahead = timedelta(hours=s.rotation_hours * (s.cycle_runs + 1))
        return start is None or start <= datetime.now(UTC) + ahead

    async def _city(self, slug: str, events: dict[str, dict[str, Any]]) -> None:
        s = self.settings
        base = f"/d/{slug}/"
        found = await self.optional(f"listing {base}all-events/", self._page(base + "all-events/", 1))
        if found is None:
            return
        pages = page_count(found)
        if pages <= s.small_city_pages:  # read whole, kept by category
            self._keep(found, events, self._relevant)
            more = await asyncio.gather(*(self._more(base + "all-events/", p) for p in range(2, pages + 1)))
            for page in more:
                self._keep(page, events, self._relevant)
        else:
            await asyncio.gather(*(
                self.optional(f"listing {base}{listing}/", self._listing(base + listing + "/", events, keep=self._relevant))
                for listing in s.listings
            ))  # fmt: skip
        if s.every_date_abroad:
            await self.optional(f"same-name events {slug}", self._same_name(events, keep=self._relevant))

    async def _listing(
        self, path: str, events: dict[str, dict[str, Any]], keep: Any, must_answer: bool = False
    ) -> None:
        """Every event of one discovery listing into ``events``, by id: every page, and past 49 pages
        by date windows."""
        s = self.settings
        first = await self._page(path, 1)
        if first is None:
            if must_answer:
                raise ParseError(f"{path}: no results after {s.empty_retries + 1} tries")
            return
        today = datetime.now(UTC).date()
        days = (today - timedelta(days=1), today + timedelta(days=s.window_days))
        await self._window(path, first, events, keep, days)

    async def _window(
        self,
        path: str,
        first: dict[str, Any],
        events: dict[str, dict[str, Any]],
        keep: Any,
        days: tuple[date, date],
        dated: bool = False,
    ) -> None:
        """A listing (its ``dated`` window ``days``) from its first page: every page when they hold all
        its events, else its two halves (a single day too full: its 49 pages)."""
        self._keep(first, events, keep)
        window = window_params(days) if dated else {}
        pages, (start, end) = page_count(first), days
        if overflows(first) and start < end:
            middle = start + (end - start) / 2
            halves = [(start, middle), (middle + timedelta(days=1), end)]
            firsts = await asyncio.gather(*(self._more(path, 1, window_params(h)) for h in halves))
            await asyncio.gather(*(
                self._window(path, f, events, keep, h, dated=True) for f, h in zip(firsts, halves, strict=True) if f
            ))  # fmt: skip
            return
        if overflows(first):
            self.log.info("%s %s: more than %d pages in one day", path, window, pages)
        for page in await asyncio.gather(*(self._more(path, p, window) for p in range(2, pages + 1))):
            self._keep(page, events, keep)

    async def _more(self, path: str, page: int, window: dict[str, str] | None = None) -> dict[str, Any] | None:
        """A later page (or a date window's first) of a listing: its failure costs that page only."""
        return await self.optional(f"{path} {window or ''} page {page}", self._page(path, page, window))

    async def _page(self, path: str, page: int, window: dict[str, str] | None = None) -> dict[str, Any] | None:
        """One discovery page's results ({pagination, results}); None when the search never answered."""
        s = self.settings
        for attempt in range(s.empty_retries + 1):
            if attempt:
                await asyncio.sleep(s.empty_retry_seconds)
            params = {**(window or {}), "page": page}
            found = search_results(await self.get_text(s.site_url + path, params=params))
            if found is not None:
                self.stats.pages += 1
                return found
        self.stats.empty += 1
        self.log.info("%s page %d: no results after %d tries", path, page, s.empty_retries + 1)
        return None

    def _keep(self, found: dict[str, Any] | None, events: dict[str, dict[str, Any]], keep: Any) -> None:
        now = datetime.now(UTC)
        for e in (found or {}).get("results") or []:
            if listable(e, now) and keep(e):
                events[str(e["id"])] = e

    def _in_israel(self, e: dict[str, Any]) -> bool:
        return _country(e) in self.settings.israel_countries

    def _relevant(self, e: dict[str, Any]) -> bool:
        return bool(set(_tag_ids(e)) & set(self.settings.relevant_tags))

    # ------------------------------------------------------------------ every date
    async def _api_events(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Every page of an API v3 events list (``{events, pagination}``)."""
        rows: list[dict[str, Any]] = []
        page = 1
        while True:
            data = await self.get_json(self.settings.site_url + path, params={**params, "page": page})
            rows += [r for r in (data or {}).get("events") or [] if isinstance(r, dict)]
            more = (data or {}).get("pagination") or {}
            if not more.get("has_more_items") or page >= int(more.get("page_count") or page):
                return rows
            page += 1

    async def _same_name(self, events: dict[str, dict[str, Any]], keep: Any) -> None:
        """The other events of the listing's same-name groups (``dedup.count`` > 1, the search shows one):
        each such organizer's live events of that name that ``keep`` takes, into ``events``."""
        groups: dict[str, dict[str, dict[str, Any]]] = {}  # organizer -> name key -> listed event
        for e in list(events.values()):
            if self._every_date(e) and dedup_count(e) > 1 and (oid := clean_str(e.get("primary_organizer_id"))):
                groups.setdefault(oid, {}).setdefault(name_key(e.get("name")), e)
        path, params = self.settings.organizer_path, {"status": "live", "expand": "venue"}
        found = await asyncio.gather(*(
            self.optional(f"organizer {oid} events", self._api_events(path.format(id=oid), params)) for oid in groups
        ))  # fmt: skip
        now = datetime.now(UTC)
        for named, rows in zip(groups.values(), found, strict=True):
            for row in rows or []:
                listed = named.get(name_key(api_name(row)))
                entry = api_entry(row, listed, self.settings) if listed else None
                due = entry and listable(entry, now) and keep(entry) and self._date_due(entry)
                if entry and due and entry["id"] not in events:
                    events[entry["id"]] = entry
                    self.stats.dates += 1

    async def _series_dates(self, listed: dict[str, Any]) -> list[dict[str, Any]]:
        """The other coming dates of a listed series event, as listing entries (none for a single date)."""
        s = self.settings
        sid = clean_str(listed.get("series_id"))
        if not sid or int(listed.get("num_children") or 1) <= 1 or sid in self._series:
            return []
        self._series.add(sid)
        params = {"time_filter": "current_future", "page_size": s.series_page_size, "expand": "venue"}
        rows = await self._api_events(s.series_path.format(id=sid), params)
        now, dates = datetime.now(UTC), []
        for row in rows:
            entry = api_entry(row, listed, s)
            due = entry and listable(entry, now) and self._every_date(entry) and self._date_due(entry)
            if entry and due and entry["id"] not in self._seen:
                self._seen.add(entry["id"])
                dates.append(entry)
        self.stats.dates += len(dates)
        return dates

    # ------------------------------------------------------------------ one event
    async def _event(self, listed: dict[str, Any]) -> list[ScrapedEvent]:
        """A listed event from its page, and in Israel its series' other dates (they share its page)."""
        page = await self.get_text(listed["url"])
        context, deal = page_context(page), offer(page)
        event = parse_event(listed, context, deal, self.settings)
        if event is None:
            return []
        self._note(listed, context)
        events = [event]
        if not self._every_date(listed):
            return events
        for entry in await self.optional(f"series {listed.get('series_id')}", self._series_dates(listed)) or []:
            try:
                other = parse_event(entry, context, deal, self.settings)
            except ParseError as exc:  # one bad date must not cost the others
                self.record_failure(entry.get("url", ""), exc)
                continue
            if other is not None:
                self._note(entry, context)
                events.append(other.model_copy(update={"is_sold_out": False}))  # the page's sale is its own date's
        return events

    def _note(self, entry: dict[str, Any], context: dict[str, Any]) -> None:
        if vid := clean_str((entry.get("primary_venue") or {}).get("id")):
            self._note_venue(f"{self.name}-{vid}", entry, context)

    def _note_venue(self, key: str, listed: dict[str, Any], context: dict[str, Any]) -> None:
        venue = listed.get("primary_venue") or {}
        place = venue.get("address") or {}
        name = clean_str(venue.get("name"))
        if not name:
            return
        seen = self._venues.setdefault(key, _Venue(
            name=name, address=clean_str(place.get("localized_address_display") or place.get("address_1")),
            latitude=to_float(place.get("latitude")), longitude=to_float(place.get("longitude")),
            profile_url=_http(venue.get("venue_profile_url")),
        ))  # fmt: skip
        organizer = (context.get("basicInfo") or {}).get("organizer") or {}
        if seen.organizer_url is None and same_name(clean_str(organizer.get("name")), name):
            seen.organizer_url = _http(organizer.get("url"))
            seen.about = html_to_text(clean_str(organizer.get("description")))


# ---------------------------------------------------------------------- rotation
def rotation_slot(now: datetime, rotation_hours: int, cycle_runs: int) -> int:
    """This run's slot: the ``rotation_hours`` window it falls in, counted modulo ``cycle_runs``."""
    return int(now.timestamp() // (rotation_hours * 3600)) % cycle_runs


def cities_due(cities: list[str], now: datetime, rotation_hours: int, cycle_runs: int) -> list[str]:
    """The cities of this run's slot: city ``i`` is due in slot ``i % cycle_runs`` (busiest first spread
    evenly over the cycle)."""
    slot = rotation_slot(now, rotation_hours, cycle_runs)
    return [c for i, c in enumerate(dict.fromkeys(cities)) if i % cycle_runs == slot]


# ---------------------------------------------------------------------- discovery pages
def server_data(page_html: str) -> dict[str, Any]:
    """The page's ``window.__SERVER_DATA__`` object ({} when missing)."""
    i = page_html.find(_MARK)
    if i < 0:
        return {}
    try:
        data, _ = json.JSONDecoder().raw_decode(page_html, i + len(_MARK))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def search_results(page_html: str) -> dict[str, Any] | None:
    """A discovery page's ``{pagination, results}``; None when its search timed out (no pagination)."""
    found = (server_data(page_html).get("search_data") or {}).get("events") or {}
    return found if isinstance(found, dict) and found.get("pagination") else None


def page_count(found: dict[str, Any]) -> int:
    return int((found.get("pagination") or {}).get("page_count") or 1)


def overflows(found: dict[str, Any]) -> bool:
    """More events than the listing's pages show (49 pages of 20)."""
    total = int((found.get("pagination") or {}).get("object_count") or 0)
    return page_count(found) >= MOST_PAGES and total > MOST_PAGES * PAGE_SIZE


def window_params(days: tuple[date, date]) -> dict[str, str]:
    return {"start_date": days[0].isoformat(), "end_date": days[1].isoformat()}


def listable(e: dict[str, Any], now: datetime) -> bool:
    """A public upcoming event at a place: not online, cancelled, password-protected or over."""
    end = _when(e, "end") or _when(e, "start")
    return bool(
        e.get("id") and e.get("url") and not e.get("is_online_event") and not e.get("is_cancelled")
        and not e.get("is_protected_event") and e.get("primary_venue") and (end is None or end > now)
    )  # fmt: skip


def soonest_first(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Listing entries by start, ascending; one without a start last."""
    far = datetime.max.replace(tzinfo=UTC)
    return sorted(events, key=lambda e: _when(e, "start") or far)


def _when(e: dict[str, Any], which: str) -> datetime | None:
    day, clock = e.get(f"{which}_date"), e.get(f"{which}_time")
    return parse_iso(f"{day}T{clock or '00:00'}", e.get("timezone")) if day else None


def _country(e: dict[str, Any]) -> str:
    return clean_str(((e.get("primary_venue") or {}).get("address") or {}).get("country")) or "IL"


def _tag_ids(e: dict[str, Any]) -> list[str]:
    return [t["tag"] for t in e.get("tags") or [] if isinstance(t, dict) and isinstance(t.get("tag"), str)]


def dedup_count(e: dict[str, Any]) -> int:
    """How many same-name events the search folded into this one (1: none)."""
    try:
        return int((e.get("dedup") or {}).get("count") or 1)
    except (TypeError, ValueError, AttributeError):
        return 1


def name_key(name: Any) -> str:
    return re.sub(r"\s+", " ", clean_str(name) or "").casefold()


def api_name(row: dict[str, Any]) -> str | None:
    name = row.get("name")
    return clean_str(name.get("text") if isinstance(name, dict) else name)


def api_entry(row: dict[str, Any], listed: dict[str, Any], s: EventbriteSettings) -> dict[str, Any] | None:
    """An API v3 event (a series date, a same-name event) as a listing entry: the listed event's, with the
    date's own id, link, name, times, place and picture. None for one that is not live, unlisted, online
    or at no place (or at another place the API did not describe)."""
    start, end = row.get("start") or {}, row.get("end") or {}
    rid, url, local = clean_str(row.get("id")), _http(row.get("url")), clean_str(start.get("local"))
    vid = clean_str(row.get("venue_id"))
    if not rid or not url or not local or not vid or row.get("is_series_parent") or row.get("online_event"):
        return None
    if (clean_str(row.get("status")) or "") not in s.api_live_statuses or row.get("listed") is False:
        return None
    day, _, clock = local.partition("T")
    end_day, _, end_clock = (clean_str(end.get("local")) or "").partition("T")
    entry = {
        **listed, "id": rid, "eid": rid, "url": url, "name": api_name(row) or listed.get("name"),
        "start_date": day, "start_time": clock[:5] or None, "end_date": end_day or None,
        "end_time": end_clock[:5] or None, "timezone": clean_str(start.get("timezone")) or listed.get("timezone"),
        "is_online_event": False, "is_cancelled": False, "dedup": None,
        "series_id": clean_str(row.get("series_id")), "num_children": 2 if row.get("is_series") else 1,
    }  # fmt: skip
    if vid != clean_str((listed.get("primary_venue") or {}).get("id")):
        place = row.get("venue")
        if not isinstance(place, dict) or not isinstance(place.get("address"), dict):
            return None
        entry["primary_venue"] = {"id": vid, "name": clean_str(place.get("name")), "address": place["address"]}
        entry["primary_venue_id"] = vid
    logo_id = clean_str(row.get("logo_id"))
    if logo_id and logo_id != clean_str(listed.get("image_id")):  # its own picture (else the listed one's file)
        picture = _http(((row.get("logo") or {}).get("original") or {}).get("url"))
        entry["image"] = {"original": {"url": picture}} if picture else {}
        entry["image_id"] = logo_id
    return entry


# ---------------------------------------------------------------------- event pages
def page_context(page_html: str) -> dict[str, Any]:
    """The event page's Next.js context (basicInfo, performers, artistInfo...); {} when missing."""
    try:
        context = next_data(page_html)["props"]["pageProps"]["context"]
    except (ParseError, ValueError, KeyError, TypeError):
        return {}
    return context if isinstance(context, dict) else {}


def offer(page_html: str) -> dict[str, Any]:
    """The event page's schema.org (Aggregate)Offer ({} when missing)."""
    return next((n for n in jsonld_nodes(page_html) if schema_types(n) & {"aggregateoffer", "offer"}), {})


def categories(tags: list[dict[str, Any]], s: EventbriteSettings, subcategory: str | None = None) -> list[str]:
    """The category words of an event's tags: its format's and subcategory's, else its category's. The
    listing leaves out some subcategories its event page names (``subcategory``: "DJ/Dance")."""
    tags = [t for t in tags if isinstance(t, dict)]
    if subcategory and not any(t.get("prefix") == "EventbriteSubCategory" for t in tags):
        tags.append({"prefix": "EventbriteSubCategory", "display_name": subcategory})
    ids = [t.get("tag", "") for t in tags]
    party = bool(set(ids) & set(s.party_formats))
    formats = [w for i in ids if i.startswith("EventbriteFormat/") for w in s.tag_genres.get(i, [])]
    subs = [
        w
        for t in tags
        if t.get("prefix") == "EventbriteSubCategory"
        for w in s.subcategory_genres.get((clean_str(t.get("display_name")) or "").casefold(), [])
    ]
    cats = [
        w for i in ids if i.startswith("EventbriteCategory/") and not (party and i in s.party_drops)
        for w in s.tag_genres.get(i, [])
    ]  # fmt: skip
    return list(dict.fromkeys([*formats, *(subs or cats)]))


def lineup(context: dict[str, Any], source: str) -> tuple[list[str], list[ArtistProfile]]:
    """The page's performers (headliners first) and the profiles of those with a picture or links."""
    performers = context.get("performers") or {}
    names = [
        n for p in [*(performers.get("headliners") or []), *(performers.get("supporters") or [])]
        if isinstance(p, dict) and (n := clean_str(p.get("name")))
    ]  # fmt: skip
    profiles = []
    for a in (context.get("artistInfo") or {}).get("artists") or []:
        if not isinstance(a, dict) or not (name := clean_str(a.get("name"))):
            continue
        names.append(name)
        links = [u for m in a.get("socialMedia") or [] if isinstance(m, dict) and (u := _http(m.get("url")))]
        image = _http(a.get("fullSizeImageUrl")) or _http(a.get("imageUrl"))
        if image or links:
            profiles.append(ArtistProfile(name=name, source=source, image_url=image, links=list(dict.fromkeys(links))))
    unique: dict[str, str] = {}
    for n in names:
        unique.setdefault(n.casefold(), n)
    return list(unique.values()), profiles


def description(e: dict[str, Any], context: dict[str, Any]) -> str | None:
    """The page's text modules, else the listing's summary."""
    modules = (context.get("structuredContent") or {}).get("modules") or []
    text = "".join(m.get("text") or "" for m in modules if isinstance(m, dict) and m.get("type") == "text")
    return html_to_text(text) or clean_str(e.get("full_description") or e.get("summary"))


def parse_event(
    e: dict[str, Any], context: dict[str, Any], deal: dict[str, Any], s: EventbriteSettings
) -> ScrapedEvent | None:
    """One event from its listing entry and its page (context and offer); None for one that won't take place."""
    title, start = clean_str(e.get("name")), _when(e, "start")
    if not title or not start:
        raise ParseError(f"{e.get('id')}: event without name or start")
    info = context.get("basicInfo") or {}
    if (
        (clean_str(info.get("status")) or "").casefold() in s.skip_statuses
        or info.get("isProtected")
        or info.get("isListed") is False
        or info.get("isOnline")
    ):
        return None
    venue = e.get("primary_venue") or {}
    place = venue.get("address") or {}
    country = _country(e)
    image = clean_str(((e.get("image") or {}).get("original") or {}).get("url"))
    offers = ((context.get("seo") or {}).get("offersSchema") or [None])[0] or deal
    low, high = to_decimal(offers.get("lowPrice") or offers.get("price")), to_decimal(offers.get("highPrice"))
    if low is None and info.get("isFree"):
        low = Decimal(0)
    sale = clean_str((context.get("salesStatus") or {}).get("salesStatus")) or ""
    sold_out = sale == "sold_out" or "soldout" in str(offers.get("availability", "")).lower()
    artists, profiles = lineup(context, EventbriteScraper.name)
    organizer = clean_str((info.get("organizer") or {}).get("name"))
    age = clean_str(((context.get("goodToKnow") or {}).get("highlights") or {}).get("ageRestriction"))
    age_match = _AGE.search(age or "")
    extra: dict[str, Any] = {"purchase_url": e["url"]}
    if vid := clean_str(venue.get("id")):
        extra[VENUE_PROFILE] = f"{EventbriteScraper.name}-{vid}"
    abroad = country != "IL"
    return ScrapedEvent(
        source=EventbriteScraper.name,
        source_event_id=str(e["id"]),
        url=e["url"],
        title=title,
        description=description(e, context),
        start_at=start,
        end_at=_when(e, "end"),
        time_known=bool(e.get("start_time")),
        venue_name=clean_str(venue.get("name")),
        address=clean_str(place.get("localized_address_display") or place.get("address_1")),
        city=clean_str(place.get("city")),
        latitude=to_float(place.get("latitude")),
        longitude=to_float(place.get("longitude")),
        country=country if abroad else None,
        time_zone=clean_str(e.get("timezone")) if abroad else None,
        source_genres=categories(
            e.get("tags") or [], s, clean_str((context.get("taxonomies") or {}).get("subcategory"))
        ),
        artists=artists,
        artist_profiles=profiles,
        min_age=int(age_match.group(1)) if age_match else None,
        organizer=organizer,
        image_url=image,
        images=[image] if image else [],
        currency=clean_str(offers.get("priceCurrency")) or clean_str(info.get("currency")) or "ILS",
        min_price=low,
        max_price=high if high is not None and high != low else None,
        is_sold_out=sold_out,
        extra=extra,
    )


# ---------------------------------------------------------------------- venues
def same_name(a: str | None, b: str | None) -> bool:
    """The same name, case, accents, spaces and punctuation aside ("LIDO Berlin" = "Lido-Berlin")."""

    def key(text: str) -> str:
        folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode() or text
        return re.sub(r"[\W_]+", "", folded.casefold())

    return bool(a and b and key(a) and key(a) == key(b))


def venue_profile(key: str, v: _Venue) -> VenueProfile:
    return VenueProfile(
        key=key,
        name=v.name,
        source=EventbriteScraper.name,
        aliases=[v.name],
        about=v.about,
        address=v.address,
        latitude=v.latitude,
        longitude=v.longitude,
        links=[v.organizer_url] if v.organizer_url else [],
        profile_url=v.profile_url or v.organizer_url,
    )


def _http(value: Any) -> str | None:
    url = clean_str(value)
    return url if url and url.startswith(("http://", "https://")) else None
