"""Tickster (tickster.co.il) - a ticket guide (concerts, theatre, kids' shows, tours, workshops) that sells through
its Kupat Bravo white label (tickster.kartisim.co.il) and Funzing's affiliate links.

The site is a React app; its data API (``/api/``) is disallowed by robots.txt, so it is not read. Each event's
page is prerendered for search engines with one schema.org ``Event`` (JSON-LD): name, start (UTC), the hall
and its city, the text, the artwork and one offer (price, and the buy link: a Kupat Bravo seance or a Funzing
experience). The sitemap (``sitemap_path``) lists every event page (~880; the category and city pages beside
them are the app's own lists, nothing prerendered). One request per page; the sitemap has no dates, so pages
are read in its order and the events yielded nearest first as they come. No coordinates: the hall and city go
to geocoding. No category either (the app's categories come only from the API): genres come from the title
and text, and the source's default when they name none.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from pydantic import Field

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, parse_iso, to_decimal, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_JSONLD = re.compile(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", re.DOTALL)
_SEANCE = re.compile(r"/announce/buy/(\d+)")


class TicksterSettings(SourceSettings):
    site_url: str = "https://www.tickster.co.il"
    sitemap_path: str = "/sitemap.xml"
    event_prefix: str = "/events/"
    max_events: int = Field(3000, ge=1)
    batch: int = 40  # pages read (and their events sorted) together


class TicksterScraper(BaseScraper[TicksterSettings]):
    name = "tickster"
    settings_model = TicksterSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        tz = zone(self.timezone)
        urls = event_urls(await self.get_text(s.site_url + s.sitemap_path), s)
        if len(urls) > s.max_events:
            self.log.warning("listing truncated at max_events=%d (%d found)", s.max_events, len(urls))
        urls = urls[: s.max_events]
        self.log.debug("listed %d event pages", len(urls))

        async def page(url: str) -> ScrapedEvent | None:
            return parse_event_page(await self.get_text(url), url, self.timezone)

        for start in range(0, len(urls), s.batch):  # nearest first within each batch of pages
            events = [e async for e in self.stream_isolated(urls[start : start + s.batch], page, lambda u: u)]
            for event in sorted(
                events, key=lambda e: (e.start_at or datetime.max.replace(tzinfo=tz), e.source_event_id)
            ):
                if is_current(event.start_at, event.end_at, RetentionSettings(), self.timezone):
                    yield event


def event_urls(sitemap: str, s: TicksterSettings) -> list[str]:
    """The sitemap's event pages: one path segment after /events/ (two segments are the app's lists)."""
    out = []
    for url in _LOC.findall(sitemap):
        path = urlparse(url).path
        if not path.startswith(s.event_prefix):
            continue
        rest = path[len(s.event_prefix) :].strip("/")
        if rest and "/" not in rest:
            out.append(url)
    return list(dict.fromkeys(out))


def parse_event_page(page_html: str, url: str, timezone: str) -> ScrapedEvent | None:
    """An event page's JSON-LD ``Event`` -> the event; None without one, a name or a start."""
    node = _event_node(page_html)
    if node is None:
        return None
    title = clean_str(node.get("name"))
    start = parse_iso(node.get("startDate"), timezone)
    if not title or start is None:
        return None
    start = start.astimezone(zone(timezone))
    location = node.get("location") if isinstance(node.get("location"), dict) else {}
    address = location.get("address") if isinstance(location.get("address"), dict) else {}
    city = clean_str(address.get("addressLocality"))
    hall = clean_str(location.get("name"))
    # "תל אביב-יפו" (the city alone) or "קלישר, תל אביב-יפו" (a street: Funzing's meeting point) is no hall.
    street = bool(hall and city and hall.endswith(f", {city}"))
    venue = None if not hall or hall == city or street else hall
    offer = node.get("offers") if isinstance(node.get("offers"), dict) else {}
    price = to_decimal(offer.get("price"))
    buy = clean_str(offer.get("url"))
    extra: dict[str, Any] = {}
    if buy and buy.startswith(("http://", "https://")):
        extra["purchase_url"] = buy
    seance = _SEANCE.search(buy or "")
    images = [i for i in node.get("image") or [] if isinstance(i, str) and i.startswith(("http://", "https://"))]
    return ScrapedEvent(
        source=TicksterScraper.name,
        source_event_id=seance.group(1) if seance else urlparse(url).path.rstrip("/").rsplit("/", 1)[-1],
        url=str(node.get("url") or url),
        title=title,
        description=clean_str(node.get("description")),
        start_at=start,
        venue_name=venue,
        address=hall if street or hall == city else ", ".join(p for p in (hall, city) if p),
        city=city,
        images=images,
        min_price=price,
        max_price=price,
        extra=extra,
    )


def _event_node(page_html: str) -> dict[str, Any] | None:
    for block in _JSONLD.findall(page_html):
        try:
            node = json.loads(block)
        except ValueError:
            continue
        for item in node if isinstance(node, list) else [node]:
            if isinstance(item, dict) and item.get("@type") == "Event":
                return item
    return None
