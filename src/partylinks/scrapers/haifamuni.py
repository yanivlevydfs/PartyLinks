"""Haifa municipality events (haifa.muni.il/culture/city-events/) - culture, sport, community.

WordPress; robots.txt allows everything, the REST API answers 401. Listing: the city-events page
holds every current event as ``div.place-archive-item`` cards (~95, some repeated across category
rails; ``data-id`` is the post id) with title, link, image and a "חינם" badge. Each event page
(``/event/<slug>/``) gives, inside ``#top_content``: one line per performance ("יום א' 25/10/2026,
שעה 20:30") or a season ("בין תאריכים: 01/09/2026 - 31/10/2026", kept as its first day, no time),
the price ("55 ₪", "חברי ההסתדרות 59 ₪ | מחיר רגיל 89 ₪", "הכניסה חופשית"), venue and street
address, categories, description and the seller's button (Haifa Theatre, Smarticket, Eventer...),
kept as ``extra["purchase_url"]`` so dedup ties it to that seller's own listing. Many events are
free and sold nowhere else. One event per performance.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import date, datetime, time
from decimal import Decimal

from selectolax.lexbor import LexborHTMLParser, LexborNode

from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import ParseError, clean_str, html_to_text, zone
from partylinks.scrapers.base import BaseScraper, SourceSettings

_SHOW = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})(?:,?\s*שעה\s*(\d{1,2}):(\d{2}))?")
_PRICE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:₪|ש\"ח)")
_FREE = ("חופשית", "ללא עלות", "חינם")


class HaifaMuniSettings(SourceSettings):
    site_url: str
    listing_path: str  # /culture/city-events/
    city: str


class HaifaMuniScraper(BaseScraper[HaifaMuniSettings]):
    name = "haifamuni"
    settings_model = HaifaMuniSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        cards = parse_listing(await self.get_text(s.site_url + s.listing_path))
        self.log.info("listed %d events", len(cards))

        async def fetch(card: tuple[str, str]) -> list[ScrapedEvent]:
            post_id, url = card
            return parse_event(await self.get_text(url), post_id, url, s.city, self.timezone)

        async for events in self.stream_isolated(cards, fetch, label=lambda c: c[1]):
            for event in events:
                yield event


def _text(node: LexborNode | None) -> str:
    return " ".join((node.text() if node else "").split())


def parse_listing(page_html: str) -> list[tuple[str, str]]:
    """(post id, page URL) of every card, each once."""
    cards: dict[str, str] = {}
    for item in LexborHTMLParser(page_html).css("div.place-archive-item[data-id]"):
        link = item.css_first("a[href]")
        if link and (url := clean_str(link.attributes.get("href"))):
            cards.setdefault(item.attributes.get("data-id") or url, url)
    return list(cards.items())


def parse_event(page_html: str, post_id: str, url: str, city: str, timezone: str) -> list[ScrapedEvent]:
    """One event per performance of the page; [] when it gives no date."""
    top = LexborHTMLParser(page_html).css_first("#top_content")
    title = _text(top.css_first("h1")) if top else ""
    if top is None or not title:
        raise ParseError(f"{url}: no event block")
    tz = zone(timezone)
    shows = []
    for line in top.css(".dates-wrapper .date-description"):
        for d, m, y, hh, mm in _SHOW.findall(_text(line))[:1]:  # a season line: its first day
            clock = time(int(hh) % 24, int(mm)) if hh else time()
            shows.append(datetime.combine(date(int(y), int(m), int(d)), clock, tz))
    if not shows:  # undated (date-wrapper left empty)
        return []
    price_text = _text(top.css_first(".price-description"))
    prices = sorted({Decimal(p) for p in _PRICE.findall(price_text)})
    free = not prices and any(word in price_text for word in _FREE)
    if free:
        tiers = [TicketTier(name="כניסה חופשית", price=0, is_free=True)]
    else:
        tiers = [TicketTier(name="כרטיס", price=p) for p in prices]
    places = [_text(p) for p in top.css(".location-description")]
    address = places[1].removesuffix(", ישראל") if len(places) > 1 else None
    if address and address.endswith(", " + city):  # "פבזנר 50, חיפה"
        address = address.removesuffix(", " + city)
    button = top.css_first("a.event-order-button")
    purchase = clean_str(button.attributes.get("href")) if button else None
    img = top.css_first(".wrap_img img")
    description = top.css_first(".event-short-desc")
    first = ScrapedEvent(
        source=HaifaMuniScraper.name,
        source_event_id=f"{post_id}:{shows[0]:%Y%m%d%H%M}",
        url=url,
        title=title,
        description=html_to_text(description.html) if description else None,
        start_at=shows[0],
        venue_name=clean_str(places[0]) if places else None,
        address=clean_str(address),
        city=city,
        source_genres=[g for g in (_text(c) for c in top.css(".category_name span")) if g],
        organizer=f"עיריית {city}",
        image_url=img.attributes.get("src") if img else None,
        min_price=Decimal(0) if free else (prices[0] if prices else None),
        max_price=Decimal(0) if free else (prices[-1] if prices else None),
        tickets=tiers,
        extra={"purchase_url": purchase} if purchase and purchase.startswith("http") else {},
    )
    return [
        first.model_copy(update={"source_event_id": f"{post_id}:{start:%Y%m%d%H%M}", "start_at": start}, deep=True)
        for start in dict.fromkeys(shows)
    ]
