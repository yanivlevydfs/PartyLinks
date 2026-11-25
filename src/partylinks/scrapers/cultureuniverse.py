"""Culture Universe (cu-cultureuniverse.co.il, יקום התרבות) - a national culture guide and box office (theatre,
kids' shows, concerts, dance, stand-up, lectures) selling Kupat Bravo seances.

A Next.js app; its ``/api/`` is disallowed by robots.txt, so it is not read. Every event page
(``/event/<id>-<slug>``) is server-rendered: one schema.org ``Event`` (JSON-LD) per performance (start, end,
hall, city, price, artwork), the React payload's ``schedules`` (each performance's id, date, time, hall, city,
sold out, and its Kupat Bravo buy link ``/announce/buy/<seance>``), the synopsis and the site's category
(``#db-category``).

Event ids come from every place that names one (the owner's rule: every category, every slug): the sitemap
(``/event/`` entries, plus its ``/category/`` and ``/events/city/`` pages), every category page
(``/category/<n>``, 1..``max_category`` - the menu's 28; each lists up to 80 events), every city page, the home
page, and an id scan of ``/event/<id>`` from ``id_scan_from`` to ``id_scan_gap`` misses past the highest known id
(pages the lists do not name; most are past shows with no date). The listing pages give each event the
category of the page it appears on. One request per event page, nearest first by id order is unknown before the
page, so pages are read in batches and each batch's events are yielded nearest first.

One event per performance; ``source_event_id`` is the schedule id. ``purchase_url`` is the seance's Kupat Bravo
buy page, so dedup merges with ``kupatbravo`` / ``israelshow`` / ``ticketsi`` / ``tickster``. No coordinates:
hall + city go to geocoding.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any
from urllib.parse import unquote, urljoin

from pydantic import Field
from selectolax.lexbor import LexborHTMLParser

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, jsonld_nodes, parse_iso, schema_types, tidy_text, to_decimal, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_FLIGHT = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', re.DOTALL)
_EVENT_ID = re.compile(r"/event/(\d+)(?:-[^\"'<>\s?#\\]*)?")


class CultureUniverseSettings(SourceSettings):
    site_url: str = "https://www.cu-cultureuniverse.co.il"
    sitemap_path: str = "/sitemap.xml"
    category_path: str = "/category/{id}"
    max_category: int = Field(40, ge=1)  # category ids tried (the menu has 1..28; 29+ answer 404)
    city_prefix: str = "/events/city/"
    event_path: str = "/event/{id}"
    buy_url: str = "https://kupatbravo.co.il{path}"  # the schedule's affLink is a Kupat Bravo path
    id_scan_from: int = Field(4400, ge=1)  # below the lowest id the site ever listed (4423, 25.11.2026)
    id_scan_gap: int = Field(40, ge=1)  # misses past the highest known id that end the scan
    batch: int = Field(40, ge=1)  # pages read (and their events sorted) together


class CultureUniverseScraper(BaseScraper[CultureUniverseSettings]):
    name = "cultureuniverse"
    settings_model = CultureUniverseSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        tz = zone(self.timezone)
        categories: dict[int, list[str]] = {}  # event id -> categories of the listing pages naming it

        def note(ids: list[int], category: str | None = None) -> None:
            for event_id in ids:
                labels = categories.setdefault(event_id, [])
                if category and category not in labels:
                    labels.append(category)

        sitemap = await self.get_text(s.site_url + s.sitemap_path)
        note(event_ids(sitemap))
        cities = [u for u in _LOC.findall(sitemap) if s.city_prefix in u]
        home = await self.optional("home page", self.get_text(s.site_url + "/"))
        if home:
            note(event_ids(home))
        for n in range(1, s.max_category + 1):
            page = await self.optional(f"category {n}", self.get_text(s.site_url + s.category_path.format(id=n)))
            if page:
                note(event_ids(page), category_name(page))
        for url in cities:
            page = await self.optional(f"city {url}", self.get_text(url))
            if page:
                note(event_ids(page))
        listed = len(categories)

        async def read(event_id: int) -> list[ScrapedEvent] | None:
            url = s.site_url + s.event_path.format(id=event_id)
            # A missing id answers 404: recorded as gone (not a fault), so the scan counts it as a miss.
            page = await self.get_text(url)
            return parse_event_page(page, url, event_id, categories.get(event_id, []), s, self.timezone)

        # Listed events first (their pages surely exist), then the id scan for pages no list names.
        found = 0
        known = sorted(categories)
        for start in range(0, len(known), s.batch):
            async for event in self._batch(known[start : start + s.batch], read, tz):
                found += 1
                yield event
        scanned = 0
        highest = max(known, default=s.id_scan_from)
        event_id, misses = s.id_scan_from, 0
        while event_id <= highest or misses < s.id_scan_gap:
            ids = [i for i in range(event_id, event_id + s.batch) if i not in categories]
            hits: set[int] = set()

            async def scan(i: int, hits: set[int] = hits) -> list[ScrapedEvent] | None:
                events = await read(i)
                if events is not None:
                    hits.add(i)
                return events

            async for event in self._batch(ids, scan, tz):
                scanned += 1
                yield event
            for i in range(event_id, event_id + s.batch):
                if i > highest:
                    misses = 0 if i in hits or i in categories else misses + 1
            highest = max([highest, *hits])
            event_id += s.batch
        self.log.info("listed %d events (%d performances); id scan added %d performances", listed, found, scanned)

    async def _batch(self, ids: list[int], read: Any, tz: Any) -> AsyncIterator[ScrapedEvent]:
        events: list[ScrapedEvent] = []
        async for batch in self.stream_isolated(ids, read, lambda i: f"event {i}"):
            events.extend(batch)
        for event in sorted(events, key=lambda e: (e.start_at or datetime.max.replace(tzinfo=tz), e.source_event_id)):
            if is_current(event.start_at, event.end_at, RetentionSettings(), self.timezone):
                yield event


def event_ids(page: str) -> list[int]:
    """Every event id a page links to (``/event/<id>-<slug>``, plain or URL-encoded, in page order)."""
    return list(dict.fromkeys(int(m.group(1)) for m in _EVENT_ID.finditer(unquote(page))))


def category_name(page: str) -> str | None:
    """A category page's name: its ``<h1>``, else its title ("הופעות מחול ובלט: הופעות ואירועים | Culture Universe")."""
    tree = LexborHTMLParser(page)
    node = tree.css_first("h1")
    name = clean_str(node.text(strip=True)) if node else None
    if not name and (title := tree.css_first("title")):
        name = clean_str(title.text().split("|")[0].split(":")[0])
    name = clean_str(name.split("|")[0].split(":")[0]) if name else None
    return name.lower() if name else None


def flight(page: str) -> str:
    """The page's React Server Components payload (``self.__next_f.push`` chunks), decoded."""
    out = []
    for chunk in _FLIGHT.findall(page):
        try:
            out.append(json.loads(f'"{chunk}"'))
        except ValueError:
            continue
    return "".join(out)


def schedules(payload: str) -> list[dict[str, Any]]:
    """The ``schedules`` array of the event's booking block: one row per performance."""
    at = payload.find('"schedules":[')
    if at < 0:
        return []
    try:
        rows, _ = json.JSONDecoder().raw_decode(payload, at + len('"schedules":'))
    except ValueError:
        return []
    return [r for r in rows if isinstance(r, dict)]


def parse_event_page(
    page: str, url: str, event_id: int, listed_in: list[str], s: CultureUniverseSettings, timezone: str
) -> list[ScrapedEvent]:
    """An event page -> one event per performance (none for a page with no coming date)."""
    tz = zone(timezone)
    tree = LexborHTMLParser(page)
    nodes = [n for n in jsonld_nodes(page) if "event" in schema_types(n)]
    by_start: dict[datetime, dict[str, Any]] = {}
    for node in nodes:
        start = parse_iso(node.get("startDate"), timezone)
        if start:
            by_start[start.astimezone(tz)] = node
    rows = schedules(flight(page))
    h1 = tree.css_first("h1")
    title = clean_str(h1.text(strip=True)) if h1 else None
    if not title and nodes:
        title = clean_str(nodes[0].get("name"))
    if not title:
        return []
    category = tree.css_first("#db-category")
    genres = list(dict.fromkeys([*([category.text(strip=True).lower()] if category else []), *listed_in]))
    synopsis = tree.css_first(".synopsis-box")
    description = None
    if synopsis:
        heading = synopsis.css_first("h2")
        if heading:
            heading.decompose()
        description = tidy_text(synopsis.text(separator="\n", strip=False), markup=False)
    canonical = tree.css_first('link[rel="canonical"]')
    page_url = (canonical.attributes.get("href") if canonical else None) or url
    events = []
    for row in rows:
        start = parse_iso(f"{row.get('date')}T{row.get('time') or '00:00'}", timezone)
        if start is None:
            continue
        start = start.astimezone(tz)
        node = by_start.get(start, nodes[0] if nodes else {})
        venue = row.get("venue") if isinstance(row.get("venue"), dict) else {}
        hall, city = clean_str(venue.get("name")), clean_str(venue.get("city"))
        offer = node.get("offers") if isinstance(node.get("offers"), dict) else {}
        price = to_decimal(offer.get("price"))
        images = [i for i in node.get("image") or [] if isinstance(i, str) and i.startswith(("http://", "https://"))]
        end = parse_iso(node.get("endDate"), timezone) if node is by_start.get(start) else None
        link = clean_str(row.get("affLink"))
        extra: dict[str, Any] = {"event_id": event_id}
        if link:
            extra["purchase_url"] = (
                link
                if link.startswith("http")
                else (s.buy_url.format(path=link) if link.startswith("/announce/") else urljoin(s.site_url, link))
            )
        events.append(
            ScrapedEvent(
                source=CultureUniverseScraper.name,
                source_event_id=str(row.get("id") or f"{event_id}-{start:%Y%m%d%H%M}"),
                url=page_url,
                title=title,
                description=description or clean_str(node.get("description")),
                start_at=start,
                end_at=end.astimezone(tz) if end else None,
                time_known=bool(row.get("time")),
                venue_name=hall if hall and hall != city else None,
                address=", ".join(dict.fromkeys(p for p in (hall, city) if p)) or None,
                city=city,
                source_genres=genres,
                images=images,
                min_price=price,
                max_price=price,
                is_sold_out=bool(row.get("isSoldOut")),
                extra=extra,
            )
        )
    return events
