"""Muzi (muzi.co.il) - community-run guide to concerts and shows across Israel (all cities).

robots.txt allows everything but ``/wp-admin/``, ``/tag/`` and one author page. The ``event``
post type is not exposed in wp-json and the city / region archives page their listing through
an admin-ajax call that posts a serialized PHP query, so the listing comes from the Yoast
sitemaps instead: ``sitemap_index.xml`` links ``event-sitemap.xml``, ``event-sitemap2.xml``...,
which list every published event page (the site removes past events; ~1,200, all cities - the
same set the ``/events-by-city/<city>/`` and ``/events-by-region/<region>/`` pages show).
Details: each ``/event/<slug>/`` page has a schema.org ``MusicEvent`` JSON-LD block (name, date
only, place, city, performer); the start time ("באיזו שעה?"), door time and price ("כמה?") come
from the page's details table, the genre from the page's ``events_by_genre-<slug>`` class, and
the seller's ticket link from the phone-capture popup's "המשך בלי להרשם" link (Eventim,
Ticketmaster, Comy, To-Mix, Funzing...), kept as ``extra["purchase_url"]`` so dedup ties the event
to that seller's own listing. Muzi sells nothing itself.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import AsyncIterator
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from urllib.parse import unquote, urlsplit

from pydantic import Field

from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import (
    ParseError,
    clean_str,
    html_to_text,
    og_image,
    schema_types,
    to_decimal,
    zone,
)
from partylinks.scrapers.base import BaseScraper, SourceSettings

_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_POST_ID = re.compile(r"\bpostid-(\d+)\b")
_PAGE_CLASSES = re.compile(r'<div class="([^"]*)" id="page-\d+"')  # the event's taxonomy classes
_GENRE = re.compile(r"\bevents_by_genre-([\w-]+)")
_CLOCK = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_DOORS = re.compile(r"פתיחת דלתות:\s*(\d{1,2}:\d{2})")
_PRICE = re.compile(r'class="single-price">(.*?)</div>', re.DOTALL)
_SHEKELS = re.compile(r"₪\s*([\d,]+(?:\.\d+)?)")
_TICKET_LINK = re.compile(r'<a href="([^"]+)"[^>]*>\s*המשך בלי להרשם')
_DESCRIPTION = re.compile(r'"description":\s*"(.*?)",(\s*\n\s*"[@\w]+":)', re.DOTALL)
_JSONLD = re.compile(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', re.DOTALL)


class MuziSettings(SourceSettings):
    site_url: str
    sitemap_path: str  # the Yoast sitemap index
    event_sitemap_prefix: str  # the index's event sitemaps: /event-sitemap
    event_path: str  # /event/
    max_events: int = Field(3000, ge=1)


class MuziScraper(BaseScraper[MuziSettings]):
    name = "muzi"
    settings_model = MuziSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        index = await self.get_text(s.site_url + s.sitemap_path)
        sitemaps = [u for u in sitemap_urls(index) if urlsplit(u).path.startswith(s.event_sitemap_prefix)]
        urls: dict[str, None] = {}
        for sitemap in sitemaps:
            page = await self.optional(f"sitemap {sitemap}", self.get_text(sitemap))
            urls.update(dict.fromkeys(event_urls(page or "", s.event_path)))
        pages = list(urls)
        if len(pages) > s.max_events:
            self.log.warning("listing truncated at max_events=%d (%d found)", s.max_events, len(pages))
        self.log.info("listed %d event pages from %d sitemaps", len(pages), len(sitemaps))
        today = datetime.now(zone(self.timezone)).date()

        async def fetch(url: str) -> ScrapedEvent | None:
            return parse_event(await self.get_text(url), url, self.timezone, today)

        async for event in self.stream_isolated(pages[: s.max_events], fetch, label=unquote):
            yield event


def sitemap_urls(xml: str) -> list[str]:
    return [html.unescape(u) for u in _LOC.findall(xml)]


def event_urls(sitemap: str, event_path: str) -> list[str]:
    """The event pages of an event sitemap (not the ``/event/`` archive itself)."""
    return [u for u in sitemap_urls(sitemap) if urlsplit(u).path.rstrip("/") not in ("", event_path.rstrip("/"))]


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value] if value else []


def _jsonld_objects(page_html: str) -> list[dict[str, Any]]:
    """The page's top-level JSON-LD objects. Not ``parsing.jsonld_nodes``: the event block's
    description is pasted unescaped (raw newlines, backslashes, quotes: ``כוכבי "מפרץ"``), so it is
    re-encoded first - everything up to the ``",`` that precedes the next key."""
    found = []
    for block in _JSONLD.findall(page_html):
        block = _DESCRIPTION.sub(lambda m: f'"description": {json.dumps(m.group(1))},{m.group(2)}', block)
        try:
            found.append(json.loads(block))
        except ValueError:
            continue
    return [n for n in found if isinstance(n, dict)]


def _row(page_html: str, label: str) -> str:
    """The value cell of the details-table row whose label is ``label`` ("באיזו שעה?")."""
    start = page_html.find(label)
    if start < 0:
        return ""
    end = page_html.find('<div class="concert_tr', start)
    return page_html[start : end if end > 0 else start + 2000]


def parse_event(page_html: str, url: str, timezone: str, today: date | None = None) -> ScrapedEvent | None:
    """The page's event; None when cancelled or already over."""
    node = next((n for n in _jsonld_objects(page_html) if schema_types(n) & {"musicevent", "event"}), None)
    if node is None:
        raise ParseError(f"{url}: no Event JSON-LD")
    if str(node.get("eventStatus") or "").endswith("EventCancelled"):
        return None
    title = clean_str(html.unescape(str(node.get("name") or "")))
    day = date.fromisoformat(str(node.get("startDate") or "")[:10]) if node.get("startDate") else None
    if not title or not day:
        raise ParseError(f"{url}: Event without name or date")
    if today and day < today:
        return None

    tz = zone(timezone)
    clock = _CLOCK.search(re.sub(r"<span.*?</span>", "", _row(page_html, "באיזו שעה?"), flags=re.DOTALL))
    start = datetime.combine(day, time(int(clock.group(1)) % 24, int(clock.group(2))) if clock else time(), tzinfo=tz)
    doors = _DOORS.search(_row(page_html, "באיזו שעה?"))

    price_html = _PRICE.search(page_html)
    price_text = html.unescape(price_html.group(1)) if price_html else ""
    amounts = (to_decimal(v.replace(",", "")) for v in _SHEKELS.findall(price_text))
    prices: list[Decimal] = [p for p in amounts if p is not None]
    if not prices and "חינם" in price_text:
        prices = [Decimal(0)]
    tickets = [TicketTier(name="כרטיס", price=p, is_free=p == 0) for p in dict.fromkeys(prices)]

    place = _dict(node.get("location"))
    address = _dict(place.get("address"))
    venue = clean_str(html.unescape(str(place.get("name") or "")))
    street = clean_str(address.get("streetAddress"))
    country = clean_str(address.get("addressCountry"))
    post_id = _POST_ID.search(page_html)
    page_classes = _PAGE_CLASSES.search(page_html)
    ticket_link = _TICKET_LINK.search(page_html)
    purchase_url = html.unescape(ticket_link.group(1)) if ticket_link else None
    if purchase_url and urlsplit(purchase_url).hostname in (None, urlsplit(url).hostname):
        purchase_url = None  # "tickets soon": the link points back to a muzi page
    image = og_image(page_html)
    return ScrapedEvent(
        source=MuziScraper.name,
        source_event_id=post_id.group(1) if post_id else urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1],
        url=url,
        title=title,
        description=html_to_text(node.get("description")),
        start_at=start,
        venue_name=venue,
        address=street if street != venue else None,
        city=clean_str(address.get("addressLocality")),
        country=country if country and country != "IL" else None,
        source_genres=_GENRE.findall(page_classes.group(1)) if page_classes else [],
        artists=[
            n for p in _list(node.get("performer")) if (n := clean_str(html.unescape(str(_dict(p).get("name") or ""))))
        ],
        # The JSON-LD images are the theme's placeholder; og:image is the event's artwork or the site icon.
        images=[image] if image and "/themes/" not in image and "favicon" not in image else [],
        min_price=min(prices, default=None),
        max_price=max(prices, default=None),
        tickets=tickets,
        extra={
            k: v
            for k, v in {
                "purchase_url": purchase_url,
                "doors_open": doors.group(1) if doors else None,
            }.items()
            if v is not None
        },
    )
