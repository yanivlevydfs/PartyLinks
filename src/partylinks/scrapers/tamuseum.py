"""Tel Aviv Museum of Art (tamuseum.org.il) - concerts, gallery talks, exhibition tours, lectures, films,
family workshops and special nights at the museum's main building and the Helena Rubinstein Pavilion.

The site's own JSON: ``/api/v2/he/events/filter/?offset=N`` (upcoming events only, 32 a page,
``meta.total_count``), grouped by day; each item has its title (with ``<i>`` tags), local start, category
(קונצרט / מפגש / סיור בתערוכה / פעילות / סרט / אירוע מיוחד) and page. A page per event (dated pages
repeat per day, read once) gives the artwork (og:image) and its own ticket link on the museum's
Smarticket box office (``tickets.tamuseum.org.il/show/<id>``; show 1366 is plain admission). That box
office's robots.txt disallows everything and its API lists only admission, so it is linked, never read:
no prices. One venue: the main building (address and point in the config). The site has no robots.txt.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Any

from pydantic import Field
from selectolax.lexbor import LexborHTMLParser

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, html_to_text, parse_iso
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_TICKETS = re.compile(r"https://tickets\.tamuseum\.org\.il/show/(\d+)")


class TaMuseumSettings(SourceSettings):
    site_url: str = "https://www.tamuseum.org.il"
    listing_path: str = "/api/v2/he/events/filter/"
    page_size: int = Field(32, ge=1)
    max_pages: int = Field(20, ge=1)
    # The box office's admission show, linked from every page: not the event's own ticket.
    admission_shows: list[str] = Field(default_factory=lambda: ["1366"])
    venue_name: str
    address: str
    city: str
    latitude: float | None = None
    longitude: float | None = None


class TaMuseumScraper(BaseScraper[TaMuseumSettings]):
    name = "tamuseum"
    settings_model = TaMuseumSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        items: list[dict[str, Any]] = []
        for page in range(s.max_pages):
            payload = await self.get_json(
                s.site_url + s.listing_path, params={"offset": page * s.page_size}, expect=dict
            )
            found = [i for day in (payload.get("items") or {}).values() for i in day or [] if isinstance(i, dict)]
            items += found
            total = (payload.get("meta") or {}).get("total_count") or 0
            if not found or len(items) >= total:
                break
        # Pages overlap (an offset counts items, a page ends on a whole day): one event per id and start.
        unique = {e.source_event_id: e for i in items if (e := listing_event(i, s, self.timezone))}
        events = list(unique.values())
        events = [e for e in events if is_current(e.start_at, e.end_at, RetentionSettings(), self.timezone)]
        self.log.debug("listed %d upcoming events", len(events))

        pages: dict[str, dict[str, Any]] = {}  # one request per page, shared by its dates

        async def read(event: ScrapedEvent) -> ScrapedEvent:
            page_url = str(event.url).split("#")[0]
            if page_url not in pages:
                pages[page_url] = page_details(await self.get_text(page_url), s.admission_shows)
            details = pages[page_url]
            event.images = details["images"]
            if details["tickets"]:
                event.extra = {**(event.extra or {}), "purchase_url": details["tickets"]}
            return event

        async for event in self.stream_isolated(events, read, lambda e: f"event {e.source_event_id}"):
            yield event


def listing_event(item: dict[str, Any], s: TaMuseumSettings, timezone: str) -> ScrapedEvent | None:
    """One listing item -> event (artwork and ticket link come from its page)."""
    title = clean_str(html_to_text(item.get("title") or ""))
    start = parse_iso(item.get("date"), timezone)
    path = clean_str(item.get("url"))
    if not title or start is None or not path:
        return None
    series = (item.get("event_series") or {}).get("title") if isinstance(item.get("event_series"), dict) else None
    return ScrapedEvent(
        source=TaMuseumScraper.name,
        source_event_id=f"{item.get('id')}:{start.isoformat()}",
        url=s.site_url + path,
        title=title,
        start_at=start,
        venue_name=s.venue_name,
        address=s.address,
        city=s.city,
        latitude=s.latitude,
        longitude=s.longitude,
        source_genres=[g for g in (clean_str(item.get("category")), clean_str(series)) if g],
    )


def page_details(page_html: str, admission_shows: list[str]) -> dict[str, Any]:
    """An event page: its artwork and its own box-office link (not the admission show)."""
    tree = LexborHTMLParser(page_html)
    image = tree.css_first('meta[property="og:image"]')
    src = clean_str(image.attributes.get("content")) if image else None
    shows = [m for m in _TICKETS.finditer(page_html) if m.group(1) not in admission_shows]
    return {
        "images": [src] if src and src.startswith("http") else [],
        "tickets": shows[0].group(0) if shows else None,
    }
