"""Eventim Israel (eventim.co.il) - concerts, theatre, shows and sport.

robots.txt disallows ``/api/`` and ``/search/``, so only public files are read.
Listing: the events sitemap (``events1_he.xml.gz``, gzip) lists every event page
as ``/event/<slug>-<id>/``. Details, all from the event page:

* ``window.dataLayer.push({... "event":"pageMeta" ...})`` - on every page: name, start/end,
  venue, city, genre / sub-genre, availability (``event_tickets_available`` "1|0").
* ticket rows (``[data-qa=tickettype]``): name (``data-tt-name``) and price.
* ``og:image``: the artwork.
* schema.org event JSON-LD - on some pages only: street address, performer and
  ``eventStatus`` (``EventCancelled`` events are dropped).

The homepage refuses non-browser clients and a browser user agent from a non-browser
client gets its connection dropped; the sitemap and event pages answer an honest agent.
"""

from __future__ import annotations

import gzip
import json
import re
import urllib.parse
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import httpx
from pydantic import Field
from selectolax.lexbor import LexborHTMLParser

from partylinks.models import ScrapedEvent, TicketTier, TierStatus
from partylinks.parsing import (
    ParseError,
    clean_str,
    html_to_text,
    jsonld_nodes,
    og_image,
    parse_iso,
    schema_types,
    to_decimal,
)
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.chillz import EVENT_TYPES

_EVENT_ID = re.compile(r"-(\d+)/?$")
_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_PUSH = re.compile(r"dataLayer\.push\(\s*(?=\{)")
_PRICE = re.compile(r"\d[\d,]*(?:\.\d+)?")


class EventimSettings(SourceSettings):
    site_url: str
    sitemap_path: str
    max_events: int = Field(1500, ge=1)
    # Event pages whose (decoded) URL holds one of these words: all when empty. Zappa uses it to
    # read only its own clubs, and Eventim skips the same words so no event is read twice.
    url_words: list[str] = Field(default_factory=list)
    skip_url_words: list[str] = Field(default_factory=list)


class EventimScraper(BaseScraper[EventimSettings]):
    name = "eventim"
    settings_model = EventimSettings

    def is_blocked(self, response: httpx.Response) -> bool:
        """Akamai answers 403 (or 429) once it rate-limits us; stop instead of hammering on."""
        return response.status_code in (403, 429)

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        urls = await self._list_urls()
        self.log.info("listed %d events", len(urls))
        async for event in self.stream_isolated(urls, self._fetch_event, label=lambda u: u):
            yield event

    async def _list_urls(self) -> list[str]:
        s = self.settings
        body = (await self.request("GET", s.site_url + s.sitemap_path)).content
        urls = sitemap_event_urls(gzip.decompress(body) if body[:2] == b"\x1f\x8b" else body)
        urls = pick_urls(urls, s.url_words, s.skip_url_words)
        if len(urls) > s.max_events:
            self.log.warning("listing truncated at max_events=%d (%d found)", s.max_events, len(urls))
        return urls[: s.max_events]

    async def _fetch_event(self, url: str) -> ScrapedEvent | None:
        return parse_event(await self.get_text(url), url, self.timezone, self.name)


class ZappaScraper(EventimScraper):
    """Zappa clubs (Herzliya, Tel Aviv Midtown, Jerusalem, Haifa) and Amphi Shuni.

    Zappa belongs to Eventim: zappa-club.co.il is served by Eventim's Akamai and its shows are
    Eventim event pages, so this reads the Eventim sitemap's pages named for Zappa (``url_words``)
    with the Eventim parser and stores them as their own source.
    """

    name = "zappa"


def pick_urls(urls: list[str], words: list[str], skip: list[str]) -> list[str]:
    def has(url: str, needles: list[str]) -> bool:
        decoded = urllib.parse.unquote(url).casefold()
        return any(n.casefold() in decoded for n in needles)

    return [u for u in urls if (not words or has(u, words)) and not has(u, skip)]


def sitemap_event_urls(xml: bytes) -> list[str]:
    return list(dict.fromkeys(u for u in _LOC.findall(xml.decode("utf-8")) if "/event/" in u and _EVENT_ID.search(u)))


def parse_event(page_html: str, url: str, timezone: str, source: str = "eventim") -> ScrapedEvent | None:
    """One event page; None when the event is cancelled."""
    meta = page_meta(page_html)
    if meta is None:
        raise ParseError(f"{url}: no pageMeta on the page")
    ld = next((n for n in jsonld_nodes(page_html) if schema_types(n) & EVENT_TYPES), {})
    if str(ld.get("eventStatus") or "").endswith("EventCancelled"):
        return None
    start = parse_iso(meta.get("event_date"), timezone)
    if start is None:
        raise ParseError(f"{url}: no event date")
    tickets = parse_tickets(page_html)
    available = str(meta.get("event_tickets_available") or "").startswith("1")
    if not available:
        tickets = [t.model_copy(update={"status": TierStatus.SOLD_OUT}) for t in tickets]
    prices = [t.price for t in tickets if t.price is not None]
    location = ld.get("location") if isinstance(ld.get("location"), dict) else {}
    address = location.get("address") if isinstance(location.get("address"), dict) else {}
    performers = ld.get("performer") or []
    offer = next((o for o in _list(ld.get("offers")) if isinstance(o, dict)), {})
    item = offer.get("itemOffered") if isinstance(offer.get("itemOffered"), dict) else {}
    image = og_image(page_html) or clean_str(meta.get("event_series_image"))
    event_id = clean_str(meta.get("event_id")) or (m.group(1) if (m := _EVENT_ID.search(url)) else url)
    return ScrapedEvent(
        source=source,
        source_event_id=event_id,
        url=url,
        title=clean_str(meta.get("event_name")) or clean_str(ld.get("name")) or "",
        description=html_to_text(item.get("description") or ld.get("description")),
        start_at=start,
        end_at=parse_iso(meta.get("event_date_end"), timezone),
        venue_name=clean_str(meta.get("event_venue_name")) or clean_str(location.get("name")),
        address=clean_str(address.get("streetAddress")),
        city=clean_str(meta.get("event_city_name")) or clean_str(address.get("addressLocality")),
        source_genres=list(dict.fromkeys(_strings(meta.get("event_series_genre")) + _strings(meta.get("event_series_sub_genre")))),
        artists=[n for p in _list(performers) if isinstance(p, dict) and (n := clean_str(p.get("name")))],
        images=[image] if image else [],
        min_price=min(prices, default=None),
        max_price=max(prices, default=None),
        is_sold_out=not available,
        tickets=tickets,
        extra={
            "series_id": clean_str(meta.get("event_serie_ID")),
            "venue_id": clean_str(meta.get("event_venue_id")),
            "booking_type": clean_str(meta.get("event_booking_type_default")),
        },
    )


def page_meta(page_html: str) -> dict[str, Any] | None:
    """The ``dataLayer.push`` object with ``"event": "pageMeta"`` (it may hold raw line breaks)."""
    decoder = json.JSONDecoder(strict=False)
    for match in _PUSH.finditer(page_html):
        try:
            obj, _ = decoder.raw_decode(page_html, match.end())
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("event") == "pageMeta":
            return obj
    return None


def parse_tickets(page_html: str) -> list[TicketTier]:
    """One tier per ticket row (name and price), identical rows merged."""
    seen: dict[tuple[str, Decimal | None], TicketTier] = {}
    for row in LexborHTMLParser(page_html).css('[data-qa="tickettype"]'):
        name = clean_str(row.attributes.get("data-tt-name")) or "כרטיס"
        price_node = row.css_first('[data-qa="tickettypeItem-price"]')
        found = _PRICE.search(price_node.text() if price_node else "")
        price = to_decimal(found.group().replace(",", "")) if found else None
        seen.setdefault((name, price), TicketTier(name=name, price=price, is_free=price == 0, status=TierStatus.ON_SALE))
    return list(seen.values())


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value] if value else []


def _strings(value: Any) -> list[str]:
    return [s for v in _list(value) if (s := clean_str(v))]
