"""Chillz (chillz.com).

Discovery: the public ``sitemap.xml`` (sitemap indexes supported), filtered to
the configured locale path and to pages modified within ``recent_days``; the ``listing_paths``
pages (the home page's carousels: featured, LGBTQ+ Events, Get Tickets) are read first and
their events fetched soonest first. The sitemap lacks some upcoming events (Mama Africa's
April 2027 festival, Nov 2026), so every organizer page it holds is also read for its
"org-upcoming" cards (the past ones are left out), and each event listed there that the
sitemap lacks is fetched after it.
Details: each page's schema.org ``Event`` JSON-LD (name, dates, venue, artwork,
performers and every ``Offer`` -> ticket tiers); pages without an Event are
profiles/venues and are skipped. An event with no Offer (no ticketing on Chillz, e.g. Callas
bar nights) whose page description (``.prose``) says "כניסה חופשית" / "Free Entrance!" /
"כניסה עלינו" gets a price-0 tier. An event with no venue has no Event JSON-LD (only a WebPage
node; ~7% of upcoming events, Nov 2026): it is read from the rendered page (the ``<h1>``, the hero's
date line, the flight data's ``ticketTypes`` with each day ticket's own day and hours).

Inbox: event pages a person opened in a browser and saved (Ctrl+S) into
``inbox_dir`` are imported first, through the same parser. Saved pages carry
their original URL (canonical link / og:url / "saved from" comment), so they get
the same ids as live-scraped pages and never duplicate.

Access: chillz.com sits behind a Vercel Security Checkpoint that refuses Python's TLS
handshake; the source speaks as Chrome (``http.impersonate``, curl_cffi), which passes.
If the checkpoint still answers, ``SourceBlockedError`` is raised on the first page
probed, so a blocked run costs two requests and is recorded as ``blocked`` (inbox
events of that run are still saved).
"""

from __future__ import annotations

import asyncio
import html as html_lib
import json
import re
import xml.etree.ElementTree as ET
from collections.abc import AsyncIterator, Iterable
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse

import httpx
from pydantic import Field
from selectolax.lexbor import LexborHTMLParser

from partylinks.artists import ArtistProfile
from partylinks.config import RetentionSettings, project_path
from partylinks.models import ScrapedEvent, TicketTier, TierStatus
from partylinks.parsing import (
    ParseError,
    clean_str,
    has_time,
    html_to_text,
    jsonld_nodes,
    parse_iso,
    schema_types,
    to_decimal,
)
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

EVENT_TYPES = {"event", "musicevent", "festival", "socialevent", "danceevent", "theaterevent", "comedyevent"}
_AVAILABILITY = {
    "soldout": TierStatus.SOLD_OUT,
    "outofstock": TierStatus.SOLD_OUT,
    "preorder": TierStatus.COMING_SOON,
    "presale": TierStatus.COMING_SOON,
    "discontinued": TierStatus.CLOSED,
}
_FREE_ENTRY = re.compile(r"כניסה (?:חופשית|חינם|ללא תשלום|עלינו)|\bfree (?:entrance|entry|admission)\b", re.IGNORECASE)
# One Next.js flight row of the page: self.__next_f.push([1,"<JSON string>"]).
_FLIGHT = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)')
_TICKET_TYPES = '"ticketTypes":['
# A listing page's grid of event cards and the section it belongs to (home carousels, organizer grids).
_ITEMS = '"items":['
_SECTION = re.compile(r'"sectionId":"([^"]*)"')
_PAST_SECTIONS = {"org-past"}
# The hero's date line: "Thursday, 24 December 2026 · 19:30 – 22:30" or
# "Wednesday, 25 November 2026 · 20:00 – Friday, 11 December 2026 · 16:00" (event's own time zone).
_DAY = re.compile(r"(\d{1,2}) ([A-Za-z]+) (\d{4})")
_CLOCK = re.compile(r"(\d{1,2}):(\d{2})\s*([AaPp][Mm])?")
_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"], 1
)}  # fmt: skip


class ChillzSettings(SourceSettings):
    site_url: str
    sitemap_path: str
    # Only sitemap URLs under this path (e.g. "/en/") and not under these prefixes.
    include_path_prefix: str
    exclude_path_prefixes: list[str] = Field(default_factory=list)
    # Listing pages read before the sitemap; their upcoming event cards are fetched first.
    listing_paths: list[str] = Field(default_factory=list)
    # Skip pages whose sitemap <lastmod> is older than this (past events / dormant profiles).
    recent_days: int = Field(120, ge=1)
    max_pages: int = Field(3000, ge=1)
    # Body markers of the bot-protection page (matched case-insensitively).
    checkpoint_markers: list[str] = Field(min_length=1)
    # Folder of event pages saved from a browser (Ctrl+S); None disables the inbox.
    inbox_dir: Path | None = None
    # Try the live site after the inbox (stops at the checkpoint until access is granted).
    live: bool = True


class ChillzScraper(BaseScraper[ChillzSettings]):
    name = "chillz"
    settings_model = ChillzSettings

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._listed: dict[str, datetime | None] = {}  # event URL -> its card's start, from listing pages

    def is_blocked(self, response: httpx.Response) -> bool:
        head = response.content[:200_000].decode("utf-8", errors="ignore").lower()
        return any(marker.lower() in head for marker in self.settings.checkpoint_markers)

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        for event in await asyncio.to_thread(self._read_inbox):
            yield event
        if not self.settings.live:
            return
        pages = await self._discover(self.settings.site_url + self.settings.sitemap_path, set())
        # The home page's cards first, soonest first (a cut run already holds this week), then the
        # sitemap's other pages. Past events are left out by the cards' dates, the sitemap's <lastmod>
        # (recent_days) and their page's date; no list of past URLs is kept (the never-past-events rule).
        self._listed.clear()
        for path in self.settings.listing_paths:
            url = self.settings.site_url + path
            if (listing := await self.optional(f"listing {url}", self.get_text(url))) is not None:
                self._list(listing)
        listed = self._soonest(self._listed)
        urls = list(dict.fromkeys([*listed, *(url for url, _ in pages)]))[: self.settings.max_pages]
        self.log.debug("discovered %d candidate pages (%d from listings)", len(urls), len(listed))
        if not urls:
            return
        async for event in self._fetch_all(urls):
            yield event
        # Events an organizer page lists that neither the sitemap nor the listings hold.
        done = set(urls)
        while extra := self._soonest(set(self._listed) - done):
            self.log.debug("%d events listed by organizer pages only", len(extra))
            done.update(extra)
            async for event in self.stream_isolated(extra, self._fetch_event, label=lambda u: u):
                yield event

    def _soonest(self, urls: Iterable[str]) -> list[str]:
        latest = datetime.max.replace(tzinfo=UTC)
        return sorted(urls, key=lambda url: (self._listed.get(url) or latest, url))

    def _list(self, page_html: str) -> None:
        """Queue the upcoming events a listing or organizer page shows."""
        for slug, start in listed_events(page_html, self.timezone):
            self._listed.setdefault(self.settings.site_url + self.settings.include_path_prefix + quote(slug, safe=""), start)

    async def _fetch_all(self, urls: list[str]) -> AsyncIterator[ScrapedEvent]:
        # Probe one page first: a checkpoint stops the run here (SourceBlockedError);
        # any other error on the probe page only skips that page.
        first = await self.optional(urls[0], self._fetch_event(urls[0]))
        if first is not None:
            yield first
        async for event in self.stream_isolated(urls[1:], self._fetch_event, label=lambda u: u):
            yield event

    def _read_inbox(self) -> list[ScrapedEvent]:
        """Import browser-saved event pages (*.html / *.htm) from ``inbox_dir``."""
        folder = project_path(self.settings.inbox_dir) if self.settings.inbox_dir else None
        if folder is None or not folder.is_dir():
            return []
        events: list[ScrapedEvent] = []
        for path in sorted(folder.glob("*.htm*")):
            try:
                page_html = path.read_text(encoding="utf-8", errors="replace")
                url = saved_page_url(page_html)
                if not url or not self._same_site(url):
                    raise ValueError("not a saved chillz.com page (no original URL found)")
                event = parse_page(page_html, url, self.timezone)
                if event is None:
                    raise ValueError("no schema.org Event on the page (profile/venue page?)")
                if self._current(event):
                    events.append(event)
            except Exception as exc:  # noqa: BLE001 - one bad file never blocks the rest
                self.failures.append(f"inbox/{path.name}: {exc}")
                self.log.warning("inbox file %s skipped: %s", path.name, exc)
        self.log.debug("inbox: %d events from %s", len(events), folder)
        return events

    async def candidate_pages(self) -> list[tuple[str, datetime | None]]:
        """Recent candidate pages from the public sitemap as (url, lastmod), newest first."""
        pages = await self._discover(self.settings.site_url + self.settings.sitemap_path, set())
        floor = datetime.min.replace(tzinfo=UTC)
        return sorted(pages, key=lambda page: page[1] or floor, reverse=True)

    async def _discover(self, sitemap_url: str, seen: set[str]) -> list[tuple[str, datetime | None]]:
        """Walk a sitemap or sitemap index and return recent candidate (url, lastmod)."""
        if sitemap_url in seen:
            return []
        seen.add(sitemap_url)
        try:
            root = ET.fromstring((await self.request("GET", sitemap_url)).content)
        except ET.ParseError as exc:
            raise ParseError(f"{sitemap_url}: not a valid sitemap") from exc
        entries = [
            (_child_text(node, "loc"), _child_text(node, "lastmod"))
            for node in root
            if _local(node.tag) in {"url", "sitemap"}
        ]
        if _local(root.tag) == "sitemapindex":
            pages: list[tuple[str, datetime | None]] = []
            for loc, _ in entries:
                if loc and self._same_site(loc):
                    # One broken child sitemap must not hide the others.
                    pages += await self.optional(f"sitemap {loc}", self._discover(loc, seen)) or []
            return pages
        cutoff = datetime.now(UTC) - timedelta(days=self.settings.recent_days)
        found: dict[str, datetime | None] = {}
        for loc, lastmod in entries:
            modified = parse_iso(lastmod) if lastmod else None
            if loc and self._is_candidate(loc) and (modified is None or modified >= cutoff):
                found[loc] = modified
        return sorted(found.items())

    def _same_site(self, url: str) -> bool:
        return urlparse(url).netloc.removeprefix("www.") == urlparse(self.settings.site_url).netloc.removeprefix("www.")

    def _is_candidate(self, url: str) -> bool:
        path = urlparse(url).path
        s = self.settings
        return (
            self._same_site(url)
            and path.startswith(s.include_path_prefix)
            and path.rstrip("/") != s.include_path_prefix.rstrip("/")
            and not any(path.startswith(p) for p in s.exclude_path_prefixes)
        )

    async def _fetch_event(self, url: str) -> ScrapedEvent | None:
        page_html = await self.get_text(url)
        event = parse_page(page_html, url, self.timezone)
        if event is None:  # an organizer (or listing) page: its upcoming cards
            self._list(page_html)
            return None
        return event if self._current(event) else None  # an ended one is over

    def _current(self, event: ScrapedEvent) -> bool:
        """Not over, today's ended hours included (the service's own retention rule)."""
        return is_current(event.start_at, event.end_at, RetentionSettings(), self.timezone, time_known=event.time_known)


_SAVED_FROM = re.compile(r"<!--\s*saved from url=\(\d+\)(\S+?)\s*-->", re.IGNORECASE)


def saved_page_url(page_html: str) -> str | None:
    """Original URL of a browser-saved page: canonical link, og:url or "saved from" comment."""
    tree = LexborHTMLParser(page_html)
    for selector, attr in (('link[rel="canonical"]', "href"), ('meta[property="og:url"]', "content")):
        node = tree.css_first(selector)
        value = node.attributes.get(attr) if node else None
        if value and value.startswith("http"):
            return value
    match = _SAVED_FROM.search(page_html[:5000])
    return match.group(1) if match else None


def parse_page(page_html: str, url: str, default_tz: str) -> ScrapedEvent | None:
    """The page's schema.org Event as a ScrapedEvent, or None for non-event pages.

    An event with no venue has no Event JSON-LD on its page (only a WebPage node): it is read from
    the rendered page instead (``_rendered_event``), the same fields shaped as schema.org."""
    event = next((n for n in jsonld_nodes(page_html) if schema_types(n) & EVENT_TYPES and n.get("name")), None)
    if event is None:
        event = _rendered_event(page_html, url)
    if event is None:
        return None
    location = _first(event.get("location"))
    address = location.get("address") if isinstance(location, dict) else None
    geo = location.get("geo") if isinstance(location, dict) else None
    currency_default = "ILS"
    tickets = [_tier(o, currency_default, default_tz) for o in _offers(event.get("offers"))]
    tree = LexborHTMLParser(page_html)
    if not tickets and any(_FREE_ENTRY.search(node.text(separator=" ")) for node in tree.css(".prose")):
        tickets = [TicketTier(name="כניסה חופשית", price=0, is_free=True)]
    priced = [t.price for t in tickets if t.price is not None and t.is_active]
    og_node = tree.css_first('meta[property="og:image"]')
    og_image = og_node.attributes.get("content") if og_node else None
    prose = tree.css_first(".prose")  # the JSON-LD's text stops at 200 characters and has no lines

    return ScrapedEvent(
        source=ChillzScraper.name,
        source_event_id=urlparse(url).path.rstrip("/").rsplit("/", 1)[-1],
        url=url,
        title=clean_str(event.get("name")) or "",
        description=(html_to_text(prose.html) if prose else None) or html_to_text(event.get("description")),
        start_at=parse_iso(event.get("startDate"), default_tz),
        time_known=has_time(event.get("startDate")),
        end_at=parse_iso(event.get("endDate"), default_tz),
        venue_name=clean_str(location.get("name")) if isinstance(location, dict) else clean_str(location),
        address=_address_text(address),
        city=_city(address),
        latitude=_float(geo.get("latitude")) if isinstance(geo, dict) else None,
        longitude=_float(geo.get("longitude")) if isinstance(geo, dict) else None,
        artists=[n for p in _as_list(event.get("performer")) if (n := clean_str(_name(p)))],
        artist_profiles=[a for p in _as_list(event.get("performer")) if (a := _performer(p))],
        organizer=clean_str(_name(_first(event.get("organizer")))),
        images=[*_images(event.get("image")), *([og_image] if og_image else [])],
        currency=next((t.currency for t in tickets), currency_default),
        min_price=min(priced, default=None),
        max_price=max(priced, default=None),
        is_sold_out=bool(tickets) and all(t.is_sold_out for t in tickets),
        tickets=tickets,
        extra={"event_status": clean_str(event.get("eventStatus")), "attendance_mode": clean_str(event.get("eventAttendanceMode"))},
    )


def _rendered_event(page_html: str, url: str) -> dict[str, Any] | None:
    """A schema.org-shaped Event from the rendered page, for event pages without Event JSON-LD.

    The page is an event page when its flight data names it (``"event_slug"``) and the hero under
    the ``<h1>`` has the calendar line; organizer and venue pages have neither. Tickets come from
    the flight data's ``ticketTypes`` (agorot; the fee is added, as in the JSON-LD's Offers)."""
    slug = unquote(urlparse(url).path.rstrip("/").rsplit("/", 1)[-1])
    flight = _flight(page_html)
    if f'"event_slug":{json.dumps(slug, ensure_ascii=False)}' not in flight:
        return None
    tree = LexborHTMLParser(page_html)
    h1 = tree.css_first("h1")
    calendar = h1.parent.css_first("svg.lucide-calendar") if h1 is not None and h1.parent is not None else None
    if h1 is None or calendar is None or calendar.parent is None:
        return None
    start, end = _date_line(calendar.parent.text(separator=" "))
    if start is None:
        return None
    meta = tree.css_first('meta[property="og:description"]')
    description = meta.attributes.get("content") if meta else None
    organizer = re.search(r'"organization_name":("(?:[^"\\]|\\.)*")', flight)
    return {
        "@type": "Event",
        "name": h1.text(separator=" "),
        "startDate": start,
        "endDate": end,
        # Without a text of its own the page says "Explore <name>. View event details and registration…".
        "description": None if description and "View event details" in description else description,
        "organizer": {"name": json.loads(organizer.group(1))} if organizer else None,
        "offers": [_offer(t) for t in _ticket_types(flight, slug) if isinstance(t, dict) and not t.get("isAddon")],
    }


def _flight(page_html: str) -> str:
    """The page's Next.js flight data (``self.__next_f`` rows), decoded and joined."""
    rows = []
    for raw in _FLIGHT.findall(page_html):
        try:
            rows.append(json.loads(raw))
        except ValueError:
            continue
    return "".join(rows)


def _ticket_types(flight: str, slug: str) -> list[Any]:
    """The ``ticketTypes`` list of this event's ticket box (the one followed by its ``eventSlug``)."""
    decoder = json.JSONDecoder()
    start = flight.find(_TICKET_TYPES)
    while start >= 0:
        try:
            types, end = decoder.raw_decode(flight, start + len(_TICKET_TYPES) - 1)
        except ValueError:
            types, end = None, start + len(_TICKET_TYPES)
        if isinstance(types, list) and f'"eventSlug":{json.dumps(slug, ensure_ascii=False)}' in flight[end : end + 400]:
            return types
        start = flight.find(_TICKET_TYPES, end)
    return []


def listed_events(page_html: str, default_tz: str) -> list[tuple[str, datetime | None]]:
    """(slug, start) of each upcoming event card a listing page shows: the home page's carousels, an
    organizer page's "org-upcoming" grid. Its "org-past" grid and the cards already over are left out.

    Each grid is an ``"items":[...]`` list in the flight data, followed by its ``sectionId``."""
    flight = _flight(page_html)
    decoder = json.JSONDecoder()
    found: dict[str, datetime | None] = {}
    start = flight.find(_ITEMS)
    while start >= 0:
        try:
            items, end = decoder.raw_decode(flight, start + len(_ITEMS) - 1)
        except ValueError:
            items, end = None, start + len(_ITEMS)
        section = _SECTION.search(flight, end, end + 1000)
        if isinstance(items, list) and (section is None or section.group(1) not in _PAST_SECTIONS):
            for item in items:
                if not isinstance(item, dict) or not str(item.get("id") or "").startswith("evt_"):
                    continue
                slug, begins = clean_str(item.get("slug")), parse_iso(item.get("startsAt"), default_tz)
                ends = parse_iso(item.get("endsAt"), default_tz)
                if slug and is_current(begins, ends, RetentionSettings(), default_tz):
                    found.setdefault(slug, begins)
        start = flight.find(_ITEMS, end)
    return list(found.items())


def _offer(ticket: dict[str, Any]) -> dict[str, Any]:
    """One ``ticketTypes`` entry as a schema.org Offer (price with the fee the buyer pays)."""
    price = ticket.get("price")
    fee = ticket.get("checkoutFees") if ticket.get("serviceFeeMode") == "pass-to-customer" else 0
    total = (Decimal(price) + Decimal(fee or 0)) / 100 if isinstance(price, int) else None
    status = f"{ticket.get('currentStatus')} {ticket.get('effectiveAvailability')}".lower()
    availability = "SoldOut" if "sold-out" in status else "PreOrder" if "soon" in status else "InStock"
    when = ticket.get("experienceDate")  # a day ticket of a multi-day event names its own day and hours
    when = when if isinstance(when, dict) else {}
    day = " ".join(c for k in ("startDate", "startTime") if (c := clean_str(when.get(k))))
    hours = "–".join(c for c in (day, clean_str(when.get("endTime"))) if c) if day else None
    return {
        "@type": "Offer",
        "name": ticket.get("name"),
        "description": "\n".join(c for c in (hours, clean_str(ticket.get("description"))) if c) or None,
        "price": 0 if ticket.get("isRsvp") else total,
        "priceCurrency": ticket.get("currency") or "ILS",
        "availability": f"https://schema.org/{availability}",
    }


def _date_line(text: str) -> tuple[str | None, str | None]:
    """ISO start / end (local, no offset) of the hero's date line; a date alone has no time."""
    first, _, second = (part.strip() for part in text.partition("–"))
    start_day, start_clock = _day(first), _clock(first)
    if start_day is None:
        return None, None
    start = datetime.combine(start_day, start_clock) if start_clock else None
    end_day, end_clock = _day(second) or start_day, _clock(second)
    end = datetime.combine(end_day, end_clock) if end_clock else None
    if start is not None and end is not None and end <= start and _day(second) is None:
        end += timedelta(days=1)  # "21:00 – 02:00": past midnight
    return (start.isoformat() if start else start_day.isoformat()), (end.isoformat() if end else None)


def _day(text: str) -> date | None:
    match = _DAY.search(text)
    month = _MONTHS.get(match.group(2).lower()) if match else None
    if match is None or month is None:
        return None
    try:
        return date(int(match.group(3)), month, int(match.group(1)))
    except ValueError:
        return None


def _clock(text: str) -> time | None:
    match = _CLOCK.search(_DAY.sub("", text))
    if match is None:
        return None
    hour, minute, half = int(match.group(1)), int(match.group(2)), (match.group(3) or "").lower()
    hour = hour % 12 + (12 if half == "pm" else 0) if half else hour
    return time(hour, minute) if hour < 24 and minute < 60 else None


def _offers(value: Any) -> list[dict[str, Any]]:
    """Flatten Offer / list of Offers / AggregateOffer(offers=...)."""
    offers: list[dict[str, Any]] = []
    for item in _as_list(value):
        if not isinstance(item, dict):
            continue
        nested = item.get("offers")
        if "aggregateoffer" in schema_types(item) and nested:
            offers += _offers(nested)
        elif "aggregateoffer" in schema_types(item):
            offers += [{**item, "price": item.get("lowPrice")}] + (
                [{**item, "price": item["highPrice"]}] if item.get("highPrice") not in (None, item.get("lowPrice")) else []
            )
        else:
            offers.append(item)
    return offers


def _tier(offer: dict[str, Any], currency: str, tz: str) -> TicketTier:
    price = to_decimal(offer.get("price"))
    availability = str(offer.get("availability") or "").rsplit("/", 1)[-1].lower()
    return TicketTier(
        name=clean_str(offer.get("name")) or clean_str(offer.get("category")) or "Ticket",
        description=clean_str(offer.get("description")),
        price=price,
        currency=clean_str(offer.get("priceCurrency")) or currency,
        is_free=price == 0,
        status=_AVAILABILITY.get(availability, TierStatus.ON_SALE),
        sale_start=parse_iso(offer.get("validFrom"), tz),
        sale_end=parse_iso(offer.get("validThrough") or offer.get("priceValidUntil"), tz),
    )


def _images(value: Any) -> list[str]:
    urls = []
    for item in _as_list(value):
        url = item.get("url") or item.get("contentUrl") if isinstance(item, dict) else item
        if isinstance(url, str) and url.startswith("http"):
            urls.append(url)
    return urls


def _address_text(address: Any) -> str | None:
    if isinstance(address, dict):
        parts = [address.get(k) for k in ("streetAddress", "addressLocality", "addressCountry")]
        return ", ".join(p for p in (clean_str(x) for x in parts) if p) or None
    return clean_str(address)


def _city(address: Any) -> str | None:
    """addressLocality, else the city part of a full "street, city, country" string
    (Chillz puts the whole address in streetAddress)."""
    if isinstance(address, dict) and (locality := clean_str(address.get("addressLocality"))):
        return locality
    parts = [p.strip() for p in (_address_text(address) or "").split(",") if p.strip()]
    return parts[-2] if len(parts) >= 3 else None


def _performer(value: Any) -> ArtistProfile | None:
    """A schema.org Person that links somewhere (bare names stay plain lineup entries)."""
    if not isinstance(value, dict) or not (name := clean_str(value.get("name"))):
        return None
    links = [u for u in [*_as_list(value.get("sameAs")), value.get("url")] if isinstance(u, str) and u.startswith("http")]
    spotify = next((u for u in links if "spotify.com" in u), None)
    other = next((u for u in links if u != spotify), None)
    image = next(iter(_images(value.get("image"))), None)
    if not (spotify or other or image):
        return None
    return ArtistProfile(name=name, source=ChillzScraper.name, image_url=image, spotify_url=spotify, profile_url=other)


def _name(value: Any) -> Any:
    return value.get("name") if isinstance(value, dict) else value


def _first(value: Any) -> Any:
    items = _as_list(value)
    return items[0] if items else None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(node: ET.Element, name: str) -> str | None:
    child = next((c for c in node if _local(c.tag) == name), None)
    return (child.text or "").strip() or None if child is not None else None


def write_links_page(
    target: Path, pages: list[tuple[str, datetime | None]], imported: set[str], inbox: Path
) -> None:
    """Static HTML checklist: open each link, Ctrl+S into the inbox; imported pages are marked."""
    rows = []
    for url, modified in pages:
        slug = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
        done = slug in imported
        rows.append(
            f'<li class="{"done" if done else ""}"><a href="{html_lib.escape(url)}" target="_blank" rel="noopener">'
            f"{html_lib.escape(slug)}</a><span>{modified.date().isoformat() if modified else ''}"
            f'{" · imported" if done else ""}</span></li>'
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Chillz inbox list</title>
<style>
:root {{ color-scheme: light dark; --bg:#fff; --fg:#111; --muted:#777; --done:#2e7d32; --link:#1a56db; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#121212; --fg:#eee; --muted:#999; --done:#81c784; --link:#8ab4f8; }} }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.5 system-ui,sans-serif; max-width:820px; margin:24px auto; padding:0 16px; }}
li {{ display:flex; justify-content:space-between; gap:12px; padding:4px 0; border-bottom:1px solid #8883; }}
a {{ color:var(--link); word-break:break-all; }} a:visited {{ color:var(--muted); }}
span {{ color:var(--muted); white-space:nowrap; font-size:13px; }}
li.done a, li.done span {{ color:var(--done); }}
code {{ font-size:13px; }}
</style></head><body>
<h1>Chillz: pages to save</h1>
<p>Open a link, press <b>Ctrl+S</b>, save as <b>Webpage, HTML Only</b> (or Complete) into
<code>{html_lib.escape(str(inbox))}</code>, then run <code>python -m partylinks scrape chillz</code>.
Visited links turn grey; imported events are green. Non-event pages (profiles, venues) are skipped on import.</p>
<p>{len(pages)} recent pages, newest first · {sum(1 for u, _ in pages if urlparse(u).path.rstrip("/").rsplit("/", 1)[-1] in imported)} imported</p>
<ol>
{chr(10).join(rows)}
</ol></body></html>""",
        encoding="utf-8",
    )
