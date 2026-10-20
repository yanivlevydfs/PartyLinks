"""Teder (teder.fm) - the radio station's nights at Beit Romano, including the Rafi room.

No API: the homepage lists the upcoming events as cards linking to ``/events/<id>``,
and each event page carries the title, a ``dd.mm.yy`` date, the start time, the room
(yard, Rafi, Teder...), the description and the artwork. Most nights are free and
never reach a ticket site; free entry is read from the description ("כניסה חופשית"),
a price from the page's price box when it has one. Selectors and labels live in config.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import datetime
from decimal import Decimal
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from selectolax.lexbor import LexborHTMLParser

from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import ParseError, clean_str, html_to_text, og_image, to_decimal
from partylinks.scrapers.base import BaseScraper, SourceSettings

_PRICE = re.compile(r"\d+(?:\.\d+)?")


class TederSelectors(BaseModel):
    card_link: str
    title: str
    date: str
    time: str
    room: str
    price: str
    description: str


class TederSettings(SourceSettings):
    site_url: str
    listing_path: str
    event_path_prefix: str
    selectors: TederSelectors
    # Phrases that mean entry is free ("כניסה חופשית").
    free_labels: list[str]
    venue_name: str
    address: str
    city: str


class TederScraper(BaseScraper[TederSettings]):
    name = "teder"
    settings_model = TederSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        urls = parse_listing(await self.get_text(s.site_url + s.listing_path), s)
        self.log.info("listed %d events", len(urls))
        async for event in self.stream_isolated(urls, self._fetch_event, label=lambda u: u):
            yield event

    async def _fetch_event(self, url: str) -> ScrapedEvent:
        return parse_event(await self.get_text(url), url, self.settings, self.timezone)


def parse_listing(page_html: str, s: TederSettings) -> list[str]:
    hrefs = (n.attributes.get("href") or "" for n in LexborHTMLParser(page_html).css(s.selectors.card_link))
    return list(dict.fromkeys(urljoin(s.site_url, h) for h in hrefs if s.event_path_prefix in h))


def parse_event(page_html: str, url: str, s: TederSettings, timezone: str) -> ScrapedEvent:
    tree = LexborHTMLParser(page_html)
    sel = s.selectors

    def text(selector: str) -> str | None:
        node = tree.css_first(selector)
        return clean_str(" ".join(node.text(separator=" ").split())) if node else None

    title = text(sel.title)
    if not title:
        raise ParseError("event title not found")
    day, time = text(sel.date), text(sel.time) or "00:00"
    if not day:
        raise ParseError("event date not found")
    start_at = datetime.strptime(f"{day} {time}", "%d.%m.%y %H:%M").replace(tzinfo=ZoneInfo(timezone))

    body = tree.css_first(sel.description)
    description = html_to_text(body.html) if body else None
    price_text = text(sel.price) or ""
    prices = sorted({p for m in _PRICE.findall(price_text) if (p := to_decimal(m)) is not None})
    free = any(label in f"{price_text} {description or ''}" for label in s.free_labels)
    tickets = [TicketTier(name="כניסה חופשית", price=Decimal(0), is_free=True)] if free else []
    tickets += [TicketTier(name="כרטיס", price=p, is_free=p == 0) for p in prices if p]
    all_prices = [t.price for t in tickets if t.price is not None]
    room = text(sel.room)
    image = og_image(page_html)
    return ScrapedEvent(
        source=TederScraper.name,
        source_event_id=url.rstrip("/").rsplit("/", 1)[-1],
        url=url,
        title=title,
        description=description,
        start_at=start_at,
        venue_name=s.venue_name,
        address=s.address,
        city=s.city,
        organizer="תדר",
        images=[image] if image else [],
        min_price=min(all_prices, default=None),
        max_price=max(all_prices, default=None),
        tickets=tickets,
        extra={"room": room},
    )
