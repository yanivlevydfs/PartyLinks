"""DICE (dice.fm) - a ticketing app for gigs, club nights, comedy, talks and more across Europe and the Americas.

The whole catalogue is read: every city and country, every kind of event DICE sells. robots.txt
allows every path to every crawler but the AI-training ones (and ``/api/``, which is not used), and
names three sitemap indexes: events, artists, venues.

- **Catalogue.** ``/sitemaps/sitemap.xml`` points to ~7 files (~275 KB gzipped each) that list every
  public upcoming event page (``/event/<slug>``, with a ``<lastmod>``) and bundle pages (multi-event
  passes, not events, skipped). ~19,300 events in Nov 2026, in ~520 towns. The slug ends in the day and
  month (``...-18th-dec-the-clapham-grand-london-tickets``), so events are ordered nearest first and
  recent past ones skipped before any event request.
- **Event data.** Each event's Next.js data route (``/_next/data/<build>/en/event/<slug>.json``, ~4 KB
  gzipped, plain HTTP; the HTML page is ~360 KB) holds the event: start and end with the venue's zone,
  the venue (name, address, real coordinates, its page, its picture), the city and country, the "from"
  price in minor units, artwork, the DICE type (``tags_types``: gig, DJ, party, comedy, talk, film...),
  the top artists (name, picture), the age limit and the description. ``<build>`` is read from the home
  page; a deploy of DICE mid-run (the route 404s with HTML) reads it again.
- **Rotation.** The full catalogue is ~19k requests, so a run reads at most ``max_pages_per_run`` pages
  in this order: events not stored yet (the stored ones are our upcoming DICE URLs, ``linked_urls``),
  events whose sitemap ``<lastmod>`` is newer than ``changed_hours``, events in the next
  ``refresh_days``, then the rotation slot (a stable hash of the slug, ``cycle_runs`` slots, one per
  ``rotation_hours``). Every stored event is read again at least once a cycle (8 runs = 4 days at two
  runs a day); a run reads only part of the catalogue, so ``unlist_missing`` is off.
- **Venues.** Every event names its venue's profile (``extra.venue_profile``). The venues of the run's
  rotation slot (at most ``venue_pages_per_run``) get their page read (``/venue/<slug>``'s data route:
  about, links, picture, coordinates) and are saved as venue profiles, so each venue is profiled once
  a cycle with its full page (a profile without the page would wipe its "about").
- **Artists.** Each event's top artists become its lineup and artist profiles (name, picture). DICE's
  artist pages (~140k in the sitemap) are not read.

DICE's types map to our categories (``type_genres``): gigs are concerts, DJ / party are club nights,
comedy is stand-up, talks are lectures, film is cinema and so on; a title or description naming a
festival adds the festival category.
"""

from __future__ import annotations

import asyncio
import json
import re
import zlib
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
from pydantic import Field

from partylinks.artists import ArtistProfile
from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier, TierStatus
from partylinks.parsing import ParseError, clean_str, parse_iso, to_float, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.venue_profiles import VENUE_PROFILE, VenueProfile

# The money of a country, for a row that names none (an unpriced row then has no price either).
LOCAL_CURRENCY = {"GB": "GBP", "US": "USD", "CA": "CAD", "AU": "AUD", "MX": "MXN", "BR": "BRL", "CH": "CHF"}
_MONTHS = {
    m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)
}
_SLUG_DATE = re.compile(r"-(\d{1,2})(?:st|nd|rd|th)-(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)(?=-|$)")
_URL = re.compile(r"<url>\s*<loc>\s*([^<\s]+)\s*</loc>(?:\s*<lastmod>\s*([^<\s]+)\s*</lastmod>)?", re.DOTALL)
_SITEMAP = re.compile(r"<sitemap>\s*<loc>\s*([^<\s]+)\s*</loc>", re.DOTALL)
_BUILD_ID = re.compile(r'"buildId"\s*:\s*"([^"]+)"')
_AGE = re.compile(r"(?<!\d)(\d{1,2})\s*\+")
_FESTIVAL = re.compile(r"\bfestivals?\b", re.IGNORECASE)


class DiceSettings(SourceSettings):
    site_url: str = "https://dice.fm"
    sitemap_url: str = "https://dice.fm/sitemaps/sitemap.xml"
    # The Next.js data routes of an event page and of a venue page ({build} from the home page).
    event_data_path: str = "/_next/data/{build}/en/event/{slug}.json"
    venue_data_path: str = "/_next/data/{build}/en/venue/{slug}.json"
    # Requests a run spends on event pages, and how the rest of the catalogue rotates (see the docstring).
    max_pages_per_run: int = 5000
    cycle_runs: int = 8
    rotation_hours: int = 12
    changed_hours: int = 36  # the sitemap is rebuilt daily (~00:10 UTC): a change is seen by the next runs
    refresh_days: int = 1  # events this close are read every run (sold out, times moved)
    past_days: int = 60  # a slug date this far back this year is past, not next year's
    venue_pages_per_run: int = 400
    # The venue's picture as its logo (a WebP of up to 960 px kept in the database per venue).
    venue_logos: bool = False
    # DICE's type (tags_types value) -> the category labels it declares (enrichment.declared_genres / genres).
    type_genres: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "music:gig": ["concert"],
            "music:dj": ["dj set", "party"],
            "music:party": ["party"],
            "music:playback": ["music"],  # an album played back to fans
            "music:artistsigning": ["meetups"],
            "culture:comedy": ["comedy"],
            "culture:social": ["community"],
            "culture:workshop": ["workshop"],
            "culture:art": ["art"],
            "culture:theatre": ["theatre"],
            "culture:talks": ["lectures"],
            "culture:podcast": ["lectures"],  # a podcast recorded live on stage
            "culture:sport": ["sport"],
            "culture:film": ["film"],
            "culture:foodanddrink": ["food"],
            "culture:wellbeing": ["wellness"],
        }
    )
    festival_genre: str = "festival"
    skip_statuses: list[str] = Field(default_factory=lambda: ["cancelled", "canceled", "postponed"])


@dataclass(frozen=True)
class Listed:
    """One event page of the sitemap."""

    slug: str
    lastmod: datetime | None
    day: date | None  # from the slug; None when it names none


class DiceScraper(BaseScraper[DiceSettings]):
    name = "dice"
    settings_model = DiceSettings
    abroad_only = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._build: str | None = None
        self._build_lock = asyncio.Lock()
        self.venues_seen: dict[str, str] = {}  # venue id -> its page slug, from this run's events

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        now = datetime.now(UTC)
        listed = await self._catalogue(now.date())
        if not listed:
            raise ParseError("the sitemaps list no event page")
        await self._read_build()
        known = known_slugs(self.linked_urls, s.site_url)
        slot = rotation_slot(now, s.rotation_hours, s.cycle_runs)
        plan, why = plan_run(listed, known, now, slot, s)
        self.log.info(
            "catalogue %d upcoming pages (%d stored); slot %d of %d; reading %d: %s",
            len(listed), len(known & {e.slug for e in listed}), slot, s.cycle_runs, len(plan), why,
        )  # fmt: skip
        retention = RetentionSettings()
        async for event in self.stream_isolated(plan, self._event, lambda e: f"event {e.slug}"):
            if is_current(event.start_at, event.end_at, retention, self.timezone):
                yield event
        await self._venues(slot)

    # ------------------------------------------------------------------ the catalogue
    async def _catalogue(self, today: date) -> list[Listed]:
        s = self.settings
        index = await self.get_text(s.sitemap_url)
        files = [u for u in _SITEMAP.findall(index) if "/artists/" not in u and "/venues/" not in u]
        if not files:
            raise ParseError("the sitemap index lists no sitemap")
        out: dict[str, Listed] = {}
        for url in files:
            xml = await self.optional(f"sitemap {url}", self.get_text(url))
            for item in sitemap_events(xml or "", s.site_url, today, s.past_days):
                out.setdefault(item.slug, item)
        return list(out.values())

    async def _read_build(self, stale: str | None = None) -> str:
        async with self._build_lock:
            if self._build is None or self._build == stale:
                build = build_id(await self.get_text(self.settings.site_url + "/"))
                if build == stale:
                    raise ParseError(f"the home page still names build {build}, whose data routes 404")
                self._build = build
            return self._build

    async def _data(self, path: str, slug: str) -> dict[str, Any] | None:
        """One data route; None when DICE removed the page. A 404 in HTML is a new DICE build: once more."""
        build = self._build or await self._read_build()
        for attempt in range(2):
            url = self.settings.site_url + path.format(build=build, slug=slug)
            try:
                response = await self.request("GET", url, headers={"x-nextjs-data": "1"})
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 404:
                    raise
                if "json" in exc.response.headers.get("content-type", "") or attempt:
                    self.log.debug("gone: %s", slug)
                    return None
                build = await self._read_build(stale=build)
                continue
            data = response.json()
            return data if isinstance(data, dict) else None
        return None

    async def _event(self, item: Listed) -> ScrapedEvent | None:
        data = await self._data(self.settings.event_data_path, item.slug)
        row = event_row(data) if data else None
        if row is None:
            return None
        event = parse_event(row, self.settings, self.timezone)
        if event is not None:
            venue = first_venue(row)
            if (vid := clean_str(venue.get("id"))) and (vslug := clean_str(venue.get("perm_name"))):
                self.venues_seen.setdefault(vid, vslug)
        return event

    # ------------------------------------------------------------------ venues
    async def _venues(self, slot: int) -> None:
        s = self.settings
        due = [(vid, slug) for vid, slug in self.venues_seen.items() if stable_slot(vid, s.cycle_runs) == slot]
        due = due[: s.venue_pages_per_run]
        self.log.info("%d venues in this run's events, %d due for their page", len(self.venues_seen), len(due))

        async def read(item: tuple[str, str]) -> VenueProfile | None:
            data = await self._data(s.venue_data_path, item[1])
            return venue_profile(data, s.site_url) if data else None

        async for profile in self.stream_isolated(due, read, lambda v: f"venue {v[1]}"):
            if s.venue_logos and profile.logo_url:
                response = await self.optional(f"venue logo {profile.key}", self.request("GET", profile.logo_url))
                if response is not None and response.headers.get("content-type", "").startswith("image/"):
                    profile.logo = response.content
            self.venue_profiles.append(profile)


# ---------------------------------------------------------------------- the catalogue
def sitemap_events(xml: str, site_url: str, today: date, past_days: int) -> list[Listed]:
    """The event pages of one sitemap file (bundles and other pages left out), without past slug dates."""
    prefix = site_url.rstrip("/") + "/event/"
    out = []
    for loc, lastmod in _URL.findall(xml):
        if not loc.startswith(prefix) or not (slug := loc[len(prefix) :].strip("/")) or "/" in slug:
            continue
        day = slug_date(slug, today, past_days)
        if day is not None and day < today:
            # Yesterday's are over or ending (a dry run at 02:00 found 210 of 300 such pages already past);
            # a late one was read by the runs before.
            continue
        out.append(Listed(slug, parse_iso(lastmod) if lastmod else None, day))
    return out


def slug_date(slug: str, today: date, past_days: int) -> date | None:
    """The day a slug names (``...-18th-dec-...``): this year's, or next year's once it is over ``past_days``
    behind. A slug names the first day of a run of dates; the event page has the real ones."""
    matches = _SLUG_DATE.findall(slug)
    if not matches:
        return None
    day, month = int(matches[-1][0]), _MONTHS[matches[-1][1]]
    for year in (today.year, today.year + 1):
        try:
            found = date(year, month, day)
        except ValueError:  # 29th feb in a common year, a typo
            continue
        if found >= today - timedelta(days=past_days):
            return found
    return None


def build_id(page_html: str) -> str:
    if m := _BUILD_ID.search(page_html):
        return m.group(1)
    raise ParseError("the home page names no Next.js buildId")


def known_slugs(urls: Iterable[str], site_url: str) -> set[str]:
    prefix = site_url.rstrip("/") + "/event/"
    return {u[len(prefix) :].split("?")[0].strip("/") for u in urls if u.startswith(prefix)}


def stable_slot(key: str, cycle: int) -> int:
    return zlib.crc32(key.encode()) % max(cycle, 1)


def rotation_slot(now: datetime, hours: int, cycle: int) -> int:
    return int(now.timestamp() // (max(hours, 1) * 3600)) % max(cycle, 1)


def plan_run(
    listed: list[Listed], known: set[str], now: datetime, slot: int, s: DiceSettings
) -> tuple[list[Listed], str]:
    """The pages a run reads, nearest first: new events, changed ones, the next days', then the rotation slot,
    in that priority up to ``max_pages_per_run``."""
    changed_after = now - timedelta(hours=s.changed_hours)
    soon = now.date() + timedelta(days=s.refresh_days)
    tiers: dict[str, list[Listed]] = {"new": [], "changed": [], "soon": [], "slot": []}
    for item in listed:
        if item.slug not in known:
            tiers["new"].append(item)
        elif item.lastmod is not None and item.lastmod >= changed_after:
            tiers["changed"].append(item)
        elif item.day is not None and item.day <= soon:
            tiers["soon"].append(item)
        elif stable_slot(item.slug, s.cycle_runs) == slot:
            tiers["slot"].append(item)
    chosen: list[Listed] = []
    counts = []
    for name, items in tiers.items():
        take = sorted(items, key=_nearest)[: max(s.max_pages_per_run - len(chosen), 0)]
        chosen += take
        counts.append(f"{name} {len(take)}/{len(items)}")
    return sorted(chosen, key=_nearest), ", ".join(counts)


def _nearest(item: Listed) -> tuple[date, str]:
    return item.day or date.max, item.slug


# ---------------------------------------------------------------------- one event
def event_row(data: dict[str, Any]) -> dict[str, Any] | None:
    """The event of an event page's data route; None for a page with none (removed, a 404 page)."""
    props = data.get("pageProps") if isinstance(data.get("pageProps"), dict) else {}
    state = props.get("initialState")
    if isinstance(state, str):
        try:
            state = json.loads(state)
        except ValueError as exc:
            raise ParseError("initialState is not JSON") from exc
    event = ((state or {}).get("event") or {}).get("event") if isinstance(state, dict) else None
    return event if isinstance(event, dict) and clean_str(event.get("id")) else None


def first_venue(row: dict[str, Any]) -> dict[str, Any]:
    return next((v for v in row.get("venues") or [] if isinstance(v, dict)), {})


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _spot(location: Any) -> tuple[float, float] | None:
    loc = _dict(location)
    lat, lng = to_float(loc.get("lat")), to_float(loc.get("lng"))
    return (lat, lng) if lat is not None and lng is not None and (lat, lng) != (0, 0) else None


def parse_event(row: dict[str, Any], s: DiceSettings, timezone: str) -> ScrapedEvent | None:
    """One event; None for one that is hidden, cancelled, online only or without a name or a start."""
    if row.get("is_hidden_event") or clean_str(row.get("status")) in s.skip_statuses:
        return None
    dates, venue = _dict(row.get("dates")), first_venue(row)
    city = _dict(venue.get("city"))
    tz = clean_str(dates.get("timezone")) or clean_str(_dict(city.get("location")).get("place")) or timezone
    start, end = parse_iso(dates.get("event_start_date"), tz), parse_iso(dates.get("event_end_date"), tz)
    title, slug = clean_str(row.get("name")), clean_str(row.get("perm_name"))
    if not title or not start or not venue or not slug:
        return None
    start, end = start.astimezone(zone(tz)), end.astimezone(zone(tz)) if end else None
    country = (clean_str(city.get("country_code")) or "").upper() or None  # one in 1500 names none
    # A venue whose pin is its city's centre (a festival over several venues) is placed by its address.
    spot = _spot(venue.get("location"))
    centre = _spot(city.get("location"))
    if spot and centre and abs(spot[0] - centre[0]) < 1e-4 and abs(spot[1] - centre[1]) < 1e-4:
        spot = None
    price = _dict(row.get("price"))
    cents = price.get("amount_from") if price.get("amount_from") is not None else price.get("amount")
    low = Decimal(int(cents)) / 100 if isinstance(cents, int | float) and cents > 0 else None
    currency = clean_str(price.get("currency")) or LOCAL_CURRENCY.get(country or "", "EUR")
    sold_out = row.get("status") == "sold-out"
    images = _dict(row.get("images"))
    image = clean_str(images.get("square") or images.get("landscape") or images.get("portrait"))
    about = _dict(row.get("about"))
    description = clean_str(about.get("description"))
    types = [t for t in row.get("tags_types") or [] if isinstance(t, dict)]
    labels = [label for t in types for label in s.type_genres.get(clean_str(t.get("value")) or "", [])]
    if _FESTIVAL.search(title) or _FESTIVAL.search(clean_str(about.get("one_liner")) or ""):
        labels.append(s.festival_genre)
    artists = [
        a
        for a in _dict(row.get("summary_lineup")).get("top_artists") or []
        if isinstance(a, dict) and clean_str(a.get("name"))
    ]
    promoter = clean_str(_dict(row.get("billing_promoter")).get("name"))
    url = f"{s.site_url}/event/{slug}"
    extra: dict[str, Any] = {"purchase_url": url}
    if vid := clean_str(venue.get("id")):
        extra[VENUE_PROFILE] = f"{DiceScraper.name}-{vid}"
    return ScrapedEvent(
        source=DiceScraper.name,
        source_event_id=row["id"],
        url=url,
        title=title,
        description=description,
        start_at=start,
        end_at=end if end and end > start else None,
        time_known=True,
        venue_name=clean_str(venue.get("name")),
        address=clean_str(venue.get("address")),
        city=clean_str(city.get("name")),
        latitude=spot[0] if spot else None,
        longitude=spot[1] if spot else None,
        country=None if country in (None, "IL") else country,
        time_zone=None if country == "IL" else tz,  # no country: still abroad (abroad_only)
        source_genres=list(dict.fromkeys(labels)),
        event_types=[n for t in types if (n := clean_str(t.get("title")))],
        artists=[a["name"].strip() for a in artists],
        artist_profiles=[
            ArtistProfile(
                name=a["name"].strip(), source=DiceScraper.name, image_url=clean_str(_dict(a.get("image")).get("url"))
            )
            for a in artists
        ],
        min_age=min_age(about),
        organizer=promoter,
        images=[image] if image else [],
        currency=currency,
        min_price=low,
        max_price=low,
        is_sold_out=sold_out,
        tickets=[
            TicketTier(
                name="Ticket",
                price=low,
                currency=currency,
                status=TierStatus.SOLD_OUT if sold_out else TierStatus.ON_SALE,
            )
        ]
        if low is not None
        else [],
        extra=extra,
    )


def min_age(about: dict[str, Any]) -> int | None:
    """``This is an 18+ event`` -> 18, from the page's age-restriction highlight."""
    for h in about.get("highlights") or []:
        if (
            isinstance(h, dict)
            and h.get("type") == "age_restriction"
            and (m := _AGE.search(clean_str(h.get("title")) or ""))
        ):
            return int(m.group(1))
    return None


# ---------------------------------------------------------------------- one venue
def venue_profile(data: dict[str, Any], site_url: str) -> VenueProfile | None:
    """A venue page's profile: about, links, picture, address and pin; None without a name or an id."""
    venue = _dict(_dict(_dict(data.get("pageProps")).get("profile")).get("venue"))
    name, vid, slug = clean_str(venue.get("name")), clean_str(venue.get("id")), clean_str(venue.get("slug"))
    if not (name and vid):
        return None
    spot = _spot(venue.get("location"))
    links = [u for u in (clean_str(v) for v in _dict(venue.get("links")).values()) if u and u.startswith("http")]
    picture = clean_str(_dict(venue.get("image")).get("url"))
    return VenueProfile(
        key=f"{DiceScraper.name}-{vid}",
        name=name,
        source=DiceScraper.name,
        aliases=[name],
        about=clean_str(venue.get("description")),
        address=clean_str(venue.get("address")),
        latitude=spot[0] if spot else None,
        longitude=spot[1] if spot else None,
        links=list(dict.fromkeys(links)),
        profile_url=f"{site_url}/venue/{slug}" if slug else None,
        logo_url=picture if picture and picture.startswith("http") else None,
    )
