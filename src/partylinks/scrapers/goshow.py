"""GoShow (goshow.co.il) - box office of Tel Aviv's small stages: AMAMA, Stand Up Factory, Comedy Bar,
Bar Giora, Enav, Beit HaMechulot, pubs and lecture halls across Israel (stand-up, jazz, talks, music).

Old ASP-style site, no API or JSON-LD. The home page lists every upcoming date (~270) as a card
(``li.carousel_row``): ``/event/<show id>/<date id>/<slug>``, a shortened title, "venue, city" and
"יום ... dd-mm-yy בשעה HH:MM". Cards dated before today are skipped there. Each date's page
(``/show/<show id>/<date id>/``, where /event/ redirects) gives the full title (og:title), artwork
(og:image), the text (``.showDesc``), the web and box-office prices and the producer ("לאירועים נוספים
של ..."). No categories: genres come from the title and text. robots.txt is empty.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import date, datetime, time
from typing import Any
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import clean_str, html_to_text, to_decimal, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_CARD_DATE = re.compile(r"(\d{1,2})-(\d{1,2})-(\d{2})\s+בשעה\s+(\d{1,2}):(\d{2})")
_HREF = re.compile(r"^/event/(\d+)/(\d+)/")
_PRICE = re.compile(r"(\d+(?:\.\d+)?)")


class GoShowSettings(SourceSettings):
    site_url: str = "https://www.goshow.co.il"
    listing_path: str = "/"
    show_path: str = "/show/{show_id}/{date_id}/"


class GoShowScraper(BaseScraper[GoShowSettings]):
    name = "goshow"
    settings_model = GoShowSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        cards = parse_listing(await self.get_text(s.site_url + s.listing_path), self.timezone)
        today = datetime.now(zone(self.timezone)).date()
        cards = [c for c in cards if c["start"].date() >= today]
        self.log.debug("listed %d upcoming dates", len(cards))

        async def read(card: dict[str, Any]) -> ScrapedEvent | None:
            event = card_event(card, s)
            add_page(event, await self.get_text(event.url.unicode_string()), s.site_url)
            current = is_current(event.start_at, event.end_at, RetentionSettings(), self.timezone)
            return event if current else None

        async for event in self.stream_isolated(cards, read, lambda c: f"date {c['date_id']}"):
            yield event


def parse_listing(page_html: str, timezone: str) -> list[dict[str, Any]]:
    """Home page -> one dict per date (first card of each date id): ids, title, venue, city, start."""
    cards: dict[str, dict[str, Any]] = {}
    for node in LexborHTMLParser(page_html).css("li.carousel_row"):
        link = node.css_first("a.closeShowDate")
        href = (link.attributes.get("href") or "") if link else ""
        if not (m := _HREF.match(href)) or m.group(2) in cards:
            continue
        when = _CARD_DATE.search(link.text() if link else "")
        if not when:
            continue
        day, month, year, hour, minute = (int(g) for g in when.groups())
        title = node.css_first(".closeTitle")
        place = node.css_first("a.closeShowTitle")
        venue, _, city = (clean_str(place.text()) or "").rpartition(",") if place else ("", "", "")
        cards[m.group(2)] = {
            "show_id": m.group(1),
            "date_id": m.group(2),
            "title": (clean_str(title.text()) or "").removesuffix("...").strip() if title else "",
            "venue": clean_str(venue) or clean_str(city),
            "city": clean_str(city) if venue else None,
            "start": datetime.combine(date(2000 + year, month, day), time(hour, minute), zone(timezone)),
        }
    return list(cards.values())


def card_event(card: dict[str, Any], s: GoShowSettings) -> ScrapedEvent:
    url = s.site_url + s.show_path.format(show_id=card["show_id"], date_id=card["date_id"])
    return ScrapedEvent(
        source=GoShowScraper.name,
        source_event_id=card["date_id"],
        url=url,
        title=card["title"],
        start_at=card["start"],
        venue_name=card["venue"],
        city=card["city"],
        address=", ".join(p for p in (card["venue"], card["city"]) if p) or None,
        extra={"purchase_url": url},  # GoShow sells the tickets itself: its page is the purchase link
    )


def add_page(event: ScrapedEvent, page_html: str, site_url: str) -> None:
    """The date's page: full title, artwork, text, prices and producer."""
    tree = LexborHTMLParser(page_html)

    def meta(prop: str) -> str | None:
        node = tree.css_first(f'meta[property="{prop}"]')
        return clean_str(node.attributes.get("content")) if node else None

    # The heading, not og:title: the site leaves quotes unescaped there ('ד"ר שי' reads as "ד").
    heading = tree.css_first(".showDescTitle h3")
    if title := (clean_str(heading.text()) if heading else None) or meta("og:title"):
        event.title = title
    if (image := meta("og:image")) and image.startswith("http"):
        event.images = [urljoin(site_url, image)]
    if (desc := tree.css_first(".showDesc")) and (text := html_to_text(desc.html)):
        event.description = text
    tiers = [
        TicketTier(name=name, price=price)
        for row in tree.css("table.orderTable tr")
        if (head := row.css_first("th"))
        and "סוג כרטיס" in head.text()  # not the fees and total rows
        and len(cells := row.css("td")) >= 2
        and (name := clean_str(cells[0].text()))
        and (price := _price(cells[1].text())) is not None
    ]
    web = next((_price(b.text()) for b in tree.css(".showDtlBox") if "מחיר באתר" in b.text()), None)
    prices = sorted({t.price for t in tiers if t.price is not None} | ({web} if web is not None else set()))
    if prices:
        event.min_price, event.max_price = prices[0], prices[-1]
        event.tickets = tiers
    producer = tree.css_first(".moreEvents a")
    if producer and (name := clean_str(producer.text())):
        event.extra = {**(event.extra or {}), "producer": name.removeprefix("לאירועים נוספים של").strip()}


def _price(text: str | None) -> Any:
    m = _PRICE.search((text or "").replace(",", ""))
    return to_decimal(m.group(1)) if m else None
