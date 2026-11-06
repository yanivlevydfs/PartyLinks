"""Tel Aviv Museum of Art (tamuseum.org.il) - concerts, gallery talks, exhibition tours, lectures, films,
family workshops and special nights at the museum's main building and the Helena Rubinstein Pavilion.

The site's own JSON: ``/api/v2/he/events/filter/?offset=N`` (upcoming events only), grouped by day.
Its ``meta.total_count`` (~74) is short of the real count and its pages overlap (an offset counts
items, a page ends on a whole day): the offset steps by 16 until a page is empty or adds no new id
and start (~11 pages, ~150 dates). Each item has its title (with ``<i>`` tags), local start, category
(קונצרט / מפגש / סיור בתערוכה / פעילות / סרט / אירוע מיוחד) and page. A page per event (dated pages
repeat per day, read once) gives the artwork (og:image), its own ticket link on the museum's
Smarticket box office (``tickets.tamuseum.org.il/show/<id>``; show 1366 is plain admission) and its
price: the "מחיר" row of the page's details table ("₪55", or one line per ticket "<name>: ₪70"); a
series page (concerts, ``#<event>`` in the URL) has that row in each event's ``li#<event>``. The box
office's robots.txt disallows everything, so it is linked, never read. One venue: the main building (address and point in the config). The site has no robots.txt.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

from pydantic import Field
from selectolax.lexbor import LexborHTMLParser

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import clean_str, html_to_text, parse_iso
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_TICKETS = re.compile(r"https://tickets\.tamuseum\.org\.il/show/(\d+)")
_SHEKELS = re.compile(r"₪\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*₪")
_PRICE_ROW = "מחיר"


class TaMuseumSettings(SourceSettings):
    site_url: str = "https://www.tamuseum.org.il"
    listing_path: str = "/api/v2/he/events/filter/"
    page_size: int = Field(16, ge=1)  # the offset's step
    max_pages: int = Field(50, ge=1)  # a guard only: the walk stops on an empty or all-seen page
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
        unique: dict[str, ScrapedEvent] = {}  # pages overlap: one event per id and start
        for page in range(s.max_pages):
            payload = await self.get_json(
                s.site_url + s.listing_path, params={"offset": page * s.page_size}, expect=dict
            )
            found = [i for day in (payload.get("items") or {}).values() for i in day or [] if isinstance(i, dict)]
            seen = len(unique)
            unique.update((e.source_event_id, e) for i in found if (e := listing_event(i, s, self.timezone)))
            if not found or len(unique) == seen:
                break
        events = list(unique.values())
        events = [e for e in events if is_current(e.start_at, e.end_at, RetentionSettings(), self.timezone)]
        self.log.debug("listed %d upcoming events", len(events))

        pages: dict[str, str] = {}  # one request per page, shared by its dates (and a series' events)

        async def read(event: ScrapedEvent) -> ScrapedEvent:
            page_url, _, fragment = str(event.url).partition("#")
            if page_url not in pages:
                pages[page_url] = await self.get_text(page_url)
            details = page_details(pages[page_url], s.admission_shows, fragment or None)
            event.images = details["images"]
            event.tickets = details["prices"]
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


def page_details(page_html: str, admission_shows: list[str], fragment: str | None = None) -> dict[str, Any]:
    """An event page: its artwork, its own box-office link (not the admission show) and its prices
    (on a series page, those of the event ``li#<fragment>``)."""
    tree = LexborHTMLParser(page_html)
    image = tree.css_first('meta[property="og:image"]')
    src = clean_str(image.attributes.get("content")) if image else None
    shows = [m for m in _TICKETS.finditer(page_html) if m.group(1) not in admission_shows]
    return {
        "images": [src] if src and src.startswith("http") else [],
        "tickets": shows[0].group(0) if shows else None,
        "prices": prices(tree, fragment),
    }


def prices(tree: LexborHTMLParser, fragment: str | None) -> list[TicketTier]:
    """The details table's "מחיר" row: "₪55", "140 ₪", or a line per ticket "<name>: ₪70"."""
    scope = tree.css_first(f'li[id="{fragment}"]') if fragment else tree.root
    tiers: list[TicketTier] = []
    for row in scope.css(".metadata_table__metadata") if scope else []:
        name = row.css_first(".metadata_table__metadata_name")
        content = row.css_first(".metadata_table__metadata_content")
        if not name or not content or name.text(strip=True) != _PRICE_ROW:
            continue
        lines = content.css(".metadata_table__metadata-content-inner") or [content]
        for line in lines:
            text = line.text(separator=" ", strip=True)
            if not (m := _SHEKELS.search(text)):
                continue
            price = Decimal(m.group(1) or m.group(2))
            label = clean_str(text[: m.start()].rstrip(" :")) if len(lines) > 1 else None
            tiers.append(TicketTier(name=label or "כרטיס", price=price, is_free=price == 0))
        break
    return tiers
