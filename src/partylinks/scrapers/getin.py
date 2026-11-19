"""GETIN (get-in.com, and its new platform getin.xyz) - an Israeli-founded ticketing platform for club
nights and festivals, now mostly abroad (Miami, New York, Toronto, Dubai, Mexico, Uruguay, Thailand,
Japan, Ibiza) plus a few Israeli nights. Producers sell on it directly, so its pages are the seller.

Listing: ``POST api.get-in.com/api/filter-events`` (the site's own call, no key) answers ~35 upcoming
events per filter, so it is asked once without a filter, once for Israel, week and trending, and once per
category from ``/api/categories`` (11); the rows are merged by id (~37 events, ~15 requests). A row gives
title, local start/end with the venue's time zone, venue (id, slug, picture, coordinates), city, country
code, price range, category ids, the cover file, and on the old platform the artists (name, photo).
Events moved to the new platform carry ``new_platform_url`` (getin.xyz/event/<id>, the same id as the
row's), their public page; the others live at get-in.com/en/<id>.

Beyond the listing (``discover_producers``): getin.xyz's sitemap names every producer page, and each
producer page's JSON-LD ItemList holds its upcoming events with their dates (past ones are not listed),
so an event the API leaves out is found there and read from its page, never a past one. Measured Nov
2026: the 53 producer pages listed the same 28 new-platform events the API gives. The sitemap's other
event URLs are past events and developer test events (Lahore / Poltava "Seats testing event"), so
they are not read.

Details: each event's page (one request each). getin.xyz pages give an Event JSON-LD with the local
time and its true offset, the venue (its /venue/<slug> page, address, coordinates), the price currency
and lowest price, the performers (each a /artist/<slug> page) and, in the page's Next state, the
event's GETIN categories (the API's English names) and currency. Old get-in.com pages give the
description and the currency (the page state's ``currency``); their JSON-LD stamps the local time with
"Z", so times always come from the row.

Artists: each performer's getin.xyz page (one request per artist this source has not profiled yet)
gives photo, Spotify link, website / Instagram / Facebook and genres (``ArtistProfile``). Venues: the
row's venue (and the page's location) give our venue profile (``VENUE_PROFILE``): name, address,
coordinates, its getin.xyz page and its picture (one request per pictured venue).

robots.txt: get-in.com has none; getin.xyz disallows /api/ (never called) and the account pages.
Requests per run: ~15 listing + 1 sitemap + ~53 producer pages + 1 per event + new artists + venue pictures.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import cache
from importlib import resources
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from pydantic import Field
from tzfpy import get_tz

from partylinks.artists import ArtistProfile
from partylinks.config import RetentionSettings
from partylinks.countries import UNKNOWN_COUNTRY, country_code
from partylinks.models import ScrapedEvent, TicketTier, TierStatus
from partylinks.parsing import (
    ParseError,
    clean_str,
    html_to_text,
    jsonld_nodes,
    parse_iso,
    schema_types,
    slugify,
    to_decimal,
    to_float,
    zone,
)
from partylinks.retention import is_current
from partylinks.scrapers.abroad import local_zone
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.venue_profiles import VENUE_PROFILE, VenueProfile

_CURRENCY = re.compile(r'"(?:priceCurrency|currency)"\s*:\s*"([A-Z]{3})"')
_EVENT_TYPES = {"event", "musicevent", "festival", "danceevent"}
_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_FLIGHT = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)')
_EVENT_ID = re.compile(r"/event/(\d+)(?:[/?#]|$)")
# Artist page links worth keeping (the rest of its *_url keys are its own pictures).
_NOT_LINKS = {"image_url", "external_url"}


class GetinSettings(SourceSettings):
    api_url: str = "https://api.get-in.com/api"
    site_url: str = "https://get-in.com"
    xyz_url: str = "https://getin.xyz"  # the new platform: event, venue, artist and producer pages
    sitemap_path: str = "/sitemap.xml"
    # Read the producer pages for upcoming events the API leaves out (~53 requests).
    discover_producers: bool = True
    # The filters asked besides "no filter": the listing caps each answer at ~35 events.
    extra_filters: list[dict[str, Any]] = Field(
        default_factory=lambda: [{"location": {"country": "IL"}}, {"type": "week"}, {"type": "trending"}]
    )
    image_base: str = "https://static.get-in.com/gallery/tablet"  # + the row's "file" (_cover_..._<id>.png)
    # A row with no category and no artist genre (a private birthday night): it is a club night.
    default_genre: str = "nightlife"
    # GETIN category names whose words name other genres as well: "indie dance" read as dance (ballet) and
    # as indie rock. Written as one word, it is indie dance alone (enrichment.genres.indie_dance).
    # "afro tech" (an artist genre) read as hi-tech: it is afro house.
    category_labels: dict[str, str] = Field(
        default_factory=lambda: {"indie dance": "indiedance", "afro tech": "afro house"}
    )
    # Artist genres (a Spotify match) dropped: a club night's "&friends" matched a metal band (Nov 2026).
    artist_genres_skip: list[str] = Field(default_factory=lambda: ["metal"])


@dataclass
class _Run:
    """What one run learns as it goes: artist pages read (by URL) and venue profiles (by key)."""

    artist_slugs: set[str] = field(default_factory=set)
    artist_pages: dict[str, asyncio.Task[ArtistProfile | None]] = field(default_factory=dict)
    venues: dict[str, VenueProfile] = field(default_factory=dict)


class GetinScraper(BaseScraper[GetinSettings]):
    name = "getin"
    settings_model = GetinSettings

    def default_headers(self) -> dict[str, str]:
        return {"X-Requested-With": "XMLHttpRequest", "Origin": self.settings.site_url}

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        url = f"{s.api_url}/filter-events"
        first = await self.post_json(url, json={"tab": "events"})
        rows = merge_rows([], first)
        if not rows:
            raise ParseError("filter-events lists no event")
        categories = await self.optional("categories", self.get_json(f"{s.api_url}/categories"))
        filters = [*s.extra_filters, *({"categories": [c["id"]]} for c in category_list(categories))]
        for body in filters:
            answer = await self.optional(f"filter {body}", self.post_json(url, json={"tab": "events", **body}))
            rows = merge_rows(rows, answer)
        names = {c["id"]: c["name"] for c in category_list(categories)}
        run = _Run()
        found: list[tuple[str, str]] = []  # (local start, event page) of upcoming events the API left out
        if s.discover_producers:
            sitemap = await self.optional("sitemap", self.get_text(s.xyz_url + s.sitemap_path))
            locs = sitemap_locs(sitemap or "", s.xyz_url)
            run.artist_slugs = set(locs["artist"])
            listed = {str(r["event_id"]) for r in rows}
            found = await self._producer_events(locs["producer"], listed)
        self.log.debug("listed %d events, %d more from producer pages", len(rows), len(found))
        retention = RetentionSettings()
        # Nearest first: the API rows and the producer pages' events in one date order.
        work: list[tuple[str, dict[str, Any] | str]] = [(str(r.get("start_date") or ""), r) for r in rows]
        work += [(day.replace("T", " "), page) for day, page in found]
        work.sort(key=lambda item: item[0])

        async def fetch(item: tuple[str, dict[str, Any] | str]) -> ScrapedEvent | None:
            _, what = item
            if isinstance(what, str):  # a producer page's event: its own page is all there is
                html = await self.get_text(what)
                event = event_from_page(html, what, s, self.timezone)
            else:
                event = parse_row(what, names, s, self.timezone)
                if event is None or not is_current(event.start_at, event.end_at, retention, self.timezone, now=_now()):
                    return None
                html = await self.optional(str(event.url), self.get_text(str(event.url)))
                event = with_page(event, html, s) if html else event
            if event is None or not is_current(event.start_at, event.end_at, retention, self.timezone, now=_now()):
                return None
            event = await self._with_artists(event, html or "", run)
            if profile := venue_of(what, html or "", s):
                run.venues.setdefault(profile.key, profile)
                event = event.model_copy(update={"extra": {**event.extra, VENUE_PROFILE: profile.key}})
            return event

        def label(item: tuple[str, dict[str, Any] | str]) -> str:
            return item[1] if isinstance(item[1], str) else str(item[1].get("event_id"))

        async for event in self.stream_isolated(work, fetch, label=label):
            yield event
        self.venue_profiles = await self._with_logos(list(run.venues.values()))
        self.log.debug("%d artist pages read, %d venue profiles", len(run.artist_pages), len(self.venue_profiles))

    async def _producer_events(self, producers: list[str], listed: set[str]) -> list[tuple[str, str]]:
        """(local start, page) of the producers' upcoming events not in ``listed``, from yesterday on."""
        since = (_now() - timedelta(days=1)).date().isoformat()

        async def read(page: str) -> list[tuple[str, str]]:
            return producer_events(await self.get_text(page), self.settings.xyz_url)

        out: dict[str, tuple[str, str]] = {}
        async for items in self.stream_isolated(producers, read, label=lambda p: p):
            for day, page in items:
                event_id = (m := _EVENT_ID.search(page)) and m.group(1)
                if event_id and event_id not in listed and day[:10] >= since:
                    out.setdefault(event_id, (day, page))
        return list(out.values())

    async def _with_artists(self, event: ScrapedEvent, html: str, run: _Run) -> ScrapedEvent:
        """The event's artists, each with its getin.xyz page's profile when it has one."""
        pages = performers(html, self.settings.xyz_url) if html else []
        names = [n for n, _ in pages] or list(event.artists)
        urls = dict(pages)
        for name in names:  # an old-platform artist: its page by the sitemap's slug for its name
            slug = slugify(name)
            if name not in urls and slug and slug in run.artist_slugs:
                urls[name] = f"{self.settings.xyz_url}/artist/{quote(slug)}"
        given = {p.name.casefold(): p for p in event.artist_profiles}
        profiles = []
        for name in names:
            base = given.get(name.casefold()) or ArtistProfile(name=name, source=self.name)
            known = self.known_artists.get(name.casefold())
            page = urls.get(name)
            if page and not (known and known.profile_url == page):
                read = await self._artist_page(page, name, run)
                base = merge_profile(base, read) if read else base
            if page and not base.profile_url:
                base.profile_url = page
            profiles.append(base)
        return event.model_copy(update={"artists": names, "artist_profiles": profiles})

    async def _artist_page(self, url: str, name: str, run: _Run) -> ArtistProfile | None:
        """One request per artist page a run: events sharing an artist wait for the same answer."""
        if url not in run.artist_pages:

            async def read() -> ArtistProfile | None:
                html = await self.optional(f"artist {url}", self.get_text(url))
                return parse_artist_page(html, url, name) if html else None

            run.artist_pages[url] = asyncio.ensure_future(read())
        profile = await run.artist_pages[url]
        if profile is None:
            return None
        return replace(profile, name=name, links=list(profile.links))

    async def _with_logos(self, profiles: list[VenueProfile]) -> list[VenueProfile]:
        async def logo(profile: VenueProfile) -> VenueProfile:
            if profile.logo_url:
                response = await self.optional(f"venue picture {profile.key}", self.request("GET", profile.logo_url))
                profile.logo = response.content if response is not None else None
            return profile

        return [p async for p in self.stream_isolated(profiles, logo, label=lambda p: p.key)]


def category_list(answer: Any) -> list[dict[str, Any]]:
    """``/api/categories``: id and English name of each category with active events."""
    rows = answer.get("data") if isinstance(answer, dict) else answer
    out = []
    for c in rows if isinstance(rows, list) else []:
        if isinstance(c, dict) and isinstance(c.get("id"), int):
            names = c.get("name") if isinstance(c.get("name"), dict) else {}
            out.append({"id": c["id"], "name": clean_str(names.get("en") or names.get("he")) or ""})
    return out


def merge_rows(rows: list[dict[str, Any]], answer: Any) -> list[dict[str, Any]]:
    """The answer's event rows added to ``rows``, once per id, in order."""
    seen = {r.get("event_id") for r in rows}
    events = answer.get("events") if isinstance(answer, dict) else None
    for row in events if isinstance(events, list) else []:
        if isinstance(row, dict) and row.get("event_id") is not None and row["event_id"] not in seen:
            seen.add(row["event_id"])
            rows.append(row)
    return rows


def genre_labels(names: list[str], s: GetinSettings) -> list[str]:
    """GETIN's category / genre names as ``source_genres``: lower case, once each, relabelled where a
    name's words would read as other genres (``category_labels``)."""
    out = (n.strip().lower() for n in names if isinstance(n, str) and n.strip())
    return list(dict.fromkeys(s.category_labels.get(n, n) for n in out))


def sitemap_locs(xml: str, xyz_url: str) -> dict[str, list[str]]:
    """getin.xyz's sitemap: producer page URLs and artist slugs, in order."""
    out: dict[str, list[str]] = {"producer": [], "artist": []}
    for loc in dict.fromkeys(_LOC.findall(xml)):
        path = urlsplit(loc).path.strip("/").split("/")
        if loc.startswith(xyz_url) and len(path) == 2 and path[0] in out:
            out[path[0]].append(loc if path[0] == "producer" else path[1])
    return out


def producer_events(html: str, xyz_url: str) -> list[tuple[str, str]]:
    """A producer page's ItemList: (local start "2027-01-20T21:00:00", event page) of its upcoming events."""
    out = []
    for node in jsonld_nodes(html):
        if (
            "event" in schema_types(node)
            and (start := clean_str(node.get("startDate")))
            and (url := clean_str(node.get("url")))
        ):
            page = _plain(url if url.startswith("http") else xyz_url + url)
            if _EVENT_ID.search(page):
                out.append((start, page))
    return list(dict.fromkeys(out))


def _venue(row: dict[str, Any]) -> dict[str, Any]:
    return row["venue"] if isinstance(row.get("venue"), dict) else {}


def parse_row(row: dict[str, Any], categories: dict[int, str], s: GetinSettings, timezone: str) -> ScrapedEvent | None:
    """One listing row; None without a title or a start."""
    venue = _venue(row)
    country = (clean_str(row.get("country_short_name")) or clean_str(venue.get("country_code")) or "").upper() or None
    abroad = country not in (None, "IL")
    city = clean_str(row.get("city")) or clean_str(venue.get("city"))
    tz = clean_str(row.get("time_zone")) or (local_zone(country, timezone, city) if abroad else timezone)
    title, start, end = clean_str(row.get("title")), _local(row.get("start_date"), tz), _local(row.get("end_date"), tz)
    if not title or not start:
        return None
    new = row.get("is_new_platform") and clean_str(row.get("new_platform_url"))
    page = _plain(new) if new else f"{s.site_url}/en/{row['event_id']}"
    lat = to_float(venue.get("lat") or row.get("lat"))
    lon = to_float(venue.get("lon") or row.get("lon"))
    images = row.get("new_platform_images") if isinstance(row.get("new_platform_images"), dict) else {}
    image = clean_str(images.get("desktop")) or (f"{s.image_base}{row['file']}" if clean_str(row.get("file")) else None)
    # New-platform rows name no artist (the page's performers do), but give each one's genres.
    artists = [a for a in row.get("artists") or [] if isinstance(a, dict) and clean_str(a.get("name"))]
    genres = [categories[c] for c in row.get("categories") or [] if categories.get(c)]
    genres += [
        g
        for a in row.get("artists") or []
        if isinstance(a, dict)
        for g in a.get("genres") or []
        if isinstance(g, str) and not any(w in g.lower() for w in s.artist_genres_skip)
    ]
    low, high = to_decimal(row.get("min_price")), to_decimal(row.get("max_price"))
    return ScrapedEvent(
        source=GetinScraper.name,
        source_event_id=str(row["event_id"]),
        url=page,
        title=title,
        start_at=start,
        end_at=end if end and end > start else None,
        time_known=True,
        venue_name=clean_str(venue.get("name"))
        or clean_str(row.get("venue") if isinstance(row.get("venue"), str) else None),
        address=clean_str(venue.get("address")) or clean_str(row.get("address")),
        city=city,
        country=country if abroad else None,
        time_zone=tz if abroad else None,
        latitude=lat if lat is not None and lon is not None else None,
        longitude=lon if lat is not None and lon is not None else None,
        source_genres=genre_labels(genres, s) or [s.default_genre],
        artists=[a["name"].strip() for a in artists],
        artist_profiles=[
            ArtistProfile(name=a["name"].strip(), source=GetinScraper.name, image_url=clean_str(a.get("image")))
            for a in artists
        ],
        images=[image] if image else [],
        organizer=clean_str(row.get("producer_name")),
        # Prices only once the page names the currency (abroad it is the venue's, not ILS).
        extra={"price_range": [str(low) if low else None, str(high) if high else None]} if low or high else {},
    )


def _event_node(page_html: str) -> dict[str, Any]:
    return next((n for n in jsonld_nodes(page_html) if schema_types(n) & _EVENT_TYPES and n.get("startDate")), {})


def with_page(event: ScrapedEvent, page_html: str, s: GetinSettings | None = None) -> ScrapedEvent:
    """The event page's description, currency (then the listing's prices) and GETIN categories."""
    node = _event_node(page_html)
    description = html_to_text(str(node.get("description") or "")) or event.description
    currency = (m := _CURRENCY.search(page_html)) and m.group(1)
    update: dict[str, Any] = {"description": description}
    low, high = (event.extra.get("price_range") or [None, None]) if event.extra else (None, None)
    if currency and (low or high):
        lo, hi = Decimal(low) if low else None, Decimal(high) if high else None
        update |= {
            "currency": currency,
            "min_price": lo or hi,
            "max_price": hi or lo,
            "tickets": [TicketTier(name="כרטיס", price=lo or hi, status=TierStatus.ON_SALE)],
        }
    s = s or GetinSettings(cron="0 0 * * *")
    if page_categories := genre_labels(page_config(page_html).get("categories") or [], s):
        # The page's categories are the row's (by name): the default stands in only when neither has one.
        known = [g for g in event.source_genres if g != s.default_genre or g in page_categories]
        update["source_genres"] = list(dict.fromkeys([*page_categories, *known]))
    update["extra"] = {k: v for k, v in (event.extra or {}).items() if k != "price_range"}
    return event.model_copy(update=update)


def event_from_page(page_html: str, url: str, s: GetinSettings, timezone: str) -> ScrapedEvent | None:
    """A getin.xyz event page alone (an event the listing left out); None without an Event JSON-LD."""
    node = _event_node(page_html)
    title, event_id = clean_str(node.get("name")), (m := _EVENT_ID.search(url)) and m.group(1)
    start = parse_iso(node.get("startDate"), timezone)
    if not node or not title or not event_id or start is None:
        return None
    place = node.get("location") if isinstance(node.get("location"), dict) else {}
    postal = place.get("address") if isinstance(place.get("address"), dict) else {}
    address = clean_str(postal.get("streetAddress")) or clean_str(
        place.get("address") if isinstance(place.get("address"), str) else None
    )
    city = clean_str(postal.get("addressLocality"))
    geo = place.get("geo") if isinstance(place.get("geo"), dict) else {}
    lat, lon = to_float(geo.get("latitude")), to_float(geo.get("longitude"))
    # The venue's zone from its coordinates; the country from the address, else from that zone.
    pinned = get_tz(lon, lat) if lat is not None and lon is not None else None
    country = address_country(address)
    if country == UNKNOWN_COUNTRY and pinned:
        country = _zone_countries().get(pinned, UNKNOWN_COUNTRY)
    abroad = country not in ("IL", "PS")
    tz = (pinned or local_zone(country, timezone, city)) if abroad else timezone
    end = parse_iso(node.get("endDate"), timezone)
    offers = node.get("offers") if isinstance(node.get("offers"), dict) else {}
    config = page_config(page_html)
    currency = clean_str(offers.get("priceCurrency")) or clean_str(config.get("currency"))
    low = to_decimal(offers.get("lowPrice"))
    images = node.get("image") if isinstance(node.get("image"), list) else [node.get("image")]
    organizer = node.get("organizer") if isinstance(node.get("organizer"), dict) else {}
    priced = bool(currency and low)
    return ScrapedEvent(
        source=GetinScraper.name,
        source_event_id=event_id,
        url=_plain(url),
        title=title,
        description=html_to_text(str(node.get("description") or "")),
        start_at=start.astimezone(zone(tz)),
        end_at=end.astimezone(zone(tz)) if end and end > start else None,
        time_known=True,
        venue_name=clean_str(place.get("name")),
        address=address,
        city=city,
        country=country if abroad else None,
        time_zone=tz if abroad else None,
        latitude=lat if lat is not None and lon is not None else None,
        longitude=lon if lat is not None and lon is not None else None,
        source_genres=genre_labels(config.get("categories") or [], s) or [s.default_genre],
        images=[i for i in (clean_str(x) for x in images) if i and i.startswith("http")][:1],
        organizer=clean_str(organizer.get("name")),
        currency=(currency or "ILS") if priced else "ILS",
        min_price=low if priced else None,
        tickets=[TicketTier(name="כרטיס", price=low, status=TierStatus.ON_SALE)] if priced else [],
    )


def address_country(address: str | None) -> str:
    """ISO country of a getin.xyz address: its last part that names one ("..., Uruguay", "Japan, 〒153...",
    "Dubai - United Arab Emirates"); ``UNKNOWN_COUNTRY`` when none does."""
    parts = [p.strip() for p in re.split(r",| - ", address or "") if p.strip()]
    return next((c for p in reversed(parts) if (c := country_code(p)) != UNKNOWN_COUNTRY), UNKNOWN_COUNTRY)


@cache
def _zone_countries() -> dict[str, str]:
    """IANA time zone -> ISO country, from tzdata's zone.tab ("Asia/Bangkok" -> "TH")."""
    table = resources.files("tzdata.zoneinfo").joinpath("zone.tab").read_text(encoding="utf-8")
    rows = (line.split("	") for line in table.splitlines() if line and not line.startswith("#"))
    return {row[2]: row[0] for row in rows if len(row) >= 3}


def _flight(page_html: str) -> str:
    """The page's Next.js flight data (``self.__next_f`` chunks), decoded and joined."""
    out = []
    for chunk in _FLIGHT.findall(page_html):
        try:
            out.append(json.loads(chunk))
        except json.JSONDecodeError:
            continue
    return "".join(out)


def _flight_object(page_html: str, key: str, first: str) -> dict[str, Any]:
    """The object under ``"<key>":`` whose first key is ``first``, in the page's flight data; {} when absent."""
    data = _flight(page_html)
    at = data.find(f'"{key}":{{"{first}":')
    if at < 0:
        return {}
    at += len(key) + 3
    try:
        value, _ = json.JSONDecoder().raw_decode(data, at)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def page_config(page_html: str) -> dict[str, Any]:
    """An event page's own config: ``{"eventId", "title", "categories": ["Techno", ...], "currency"}``."""
    return _flight_object(page_html, "config", "eventId")


def performers(page_html: str, xyz_url: str) -> list[tuple[str, str | None]]:
    """(name, artist page) of each performer in the event page's JSON-LD."""
    out: dict[str, str | None] = {}
    raw = _event_node(page_html).get("performer")
    for p in raw if isinstance(raw, list) else [raw]:
        if isinstance(p, dict) and (name := clean_str(p.get("name"))):
            url = clean_str(p.get("url"))
            out.setdefault(name, _plain(url if url.startswith("http") else xyz_url + url) if url else None)
    return list(out.items())


def parse_artist_page(page_html: str, url: str, name: str) -> ArtistProfile | None:
    """A getin.xyz artist page: its Next state's artist (photo, Spotify, links, genres), else its Person JSON-LD."""
    artist = _flight_object(page_html, "artist", "id")
    person = next((n for n in jsonld_nodes(page_html) if "person" in schema_types(n)), {})
    artist = artist if isinstance(artist, dict) else {}
    if not artist and not person:
        return None
    external = clean_str(artist.get("external_url"))
    links = [clean_str(v) for k, v in artist.items() if k.endswith("_url") and k not in _NOT_LINKS]
    same = person.get("sameAs") if isinstance(person.get("sameAs"), list) else []
    links += [clean_str(v) for v in same]
    if external and "spotify.com" not in external:
        links.append(external)
    genres = [g for g in (clean_str(x) for x in artist.get("genres") or []) if g]
    return ArtistProfile(
        name=name,
        source=GetinScraper.name,
        genre=genres[0] if genres else None,
        image_url=clean_str(artist.get("image_url")) or clean_str(person.get("image")),
        spotify_url=external if external and "spotify.com" in external else None,
        profile_url=url,
        links=list(dict.fromkeys(u for u in links if u and u.startswith("http"))),
    )


def merge_profile(base: ArtistProfile, page: ArtistProfile) -> ArtistProfile:
    """The page's profile, with what only the listing gave (its photo) kept."""
    return ArtistProfile(
        name=base.name,
        source=base.source,
        kind=page.kind or base.kind,
        genre=page.genre or base.genre,
        image_url=page.image_url or base.image_url,
        spotify_url=page.spotify_url or base.spotify_url,
        profile_url=page.profile_url or base.profile_url,
        links=list(dict.fromkeys([*page.links, *base.links])),
    )


def venue_of(what: dict[str, Any] | str, page_html: str, s: GetinSettings) -> VenueProfile | None:
    """The event's venue as a profile: the row's venue (id, slug, picture), else the page's location."""
    venue = _venue(what) if isinstance(what, dict) else {}
    place = _event_node(page_html).get("location") if page_html else None
    place = place if isinstance(place, dict) else {}
    page_slug = urlsplit(clean_str(place.get("url")) or "").path.rstrip("/").rpartition("/venue/")[2]
    slug = clean_str(venue.get("slug")) or clean_str(page_slug)
    name = clean_str(venue.get("name")) or clean_str(place.get("name"))
    if not slug or not name:
        return None
    postal = place.get("address") if isinstance(place.get("address"), dict) else {}
    geo = place.get("geo") if isinstance(place.get("geo"), dict) else {}
    lat = to_float(venue.get("lat")) if venue.get("lat") is not None else to_float(geo.get("latitude"))
    lon = to_float(venue.get("lon")) if venue.get("lon") is not None else to_float(geo.get("longitude"))
    return VenueProfile(
        key=f"{GetinScraper.name}-{quote(slug, safe='-_.')}",
        name=name,
        source=GetinScraper.name,
        aliases=[name],
        address=clean_str(venue.get("address")) or clean_str(postal.get("streetAddress")),
        latitude=lat if lat is not None and lon is not None else None,
        longitude=lon if lat is not None and lon is not None else None,
        profile_url=f"{s.xyz_url}/venue/{quote(slug)}",
        logo_url=clean_str(venue.get("image_url")),
    )


def _now() -> datetime:
    return datetime.now(UTC)


def _local(value: Any, tz: str) -> datetime | None:
    """ "2026-12-02 20:00:00" in the venue's zone."""
    text = clean_str(value)
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=zone(tz)) if text else None
    except ValueError:
        return None


def _plain(url: str) -> str:
    """The page without the platform's seller code (?seller_code=getin)."""
    return urlunsplit(urlsplit(url)._replace(query="", fragment=""))
