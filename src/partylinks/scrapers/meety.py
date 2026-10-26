"""Meety / מפגשים ובילויים (meety.co.il) - social meetups across Israel: salsa and bachata nights, folk dancing,
running and walking groups, karaoke, board games, tastings.

Laravel, server-rendered. ``/events`` lists the coming week (about 150 cards: link, title, artwork,
``data-eventdate`` "2026-10-26 21:00:00"); weekly events show their next date, so the rolling window keeps
them current. The page's "תחום עניין" select (``i``) names the categories; each ``/events?i=<value>``
listing tags its events with that category's Hebrew label (``salsa`` "סלסה- ריקודים לטיניים", ``folk dances``
"ריקודי עם"). The event page adds the text and the address (the "נווט לאירוע" Google Maps link, the city
last). No prices on the site.
"""

from __future__ import annotations

import html
import re
from collections.abc import AsyncIterator
from datetime import datetime
from urllib.parse import parse_qs, quote, quote_plus, urlsplit

from selectolax.lexbor import LexborHTMLParser

from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, html_to_text, zone
from partylinks.scrapers.base import BaseScraper, SourceSettings

_STAMP = re.compile(r"(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})")
_ID = re.compile(r"^/events/(\d+)$")
_MAPS = re.compile(r"https://maps\.google\.com/\?q=[^\"'\s>]+")
_DEFAULT_IMAGE = "/images/def-banner.png"


class MeetySettings(SourceSettings):
    site_url: str = "https://meety.co.il"


class MeetyScraper(BaseScraper[MeetySettings]):
    name = "meety"
    settings_model = MeetySettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        base = self.settings.site_url
        first = await self.get_text(f"{base}/events")
        cards = parse_listing(first)
        for value, label in parse_interests(first):
            page = await self.optional(f"interest {value}", self.get_text(f"{base}/events?i={quote_plus(value)}"))
            for event_id in parse_listing(page or ""):
                cards.setdefault(event_id, []).append(label)
        self.log.info("listed %d events", len(cards))

        async def detail(item: tuple[str, list[str]]) -> ScrapedEvent | None:
            event_id, genres = item
            url = f"{base}/events/{event_id}"
            return parse_event(event_id, url, await self.get_text(url), genres, base, self.timezone)

        async for event in self.stream_isolated(cards.items(), detail, lambda item: f"event {item[0]}"):
            yield event


def parse_interests(page_html: str) -> list[tuple[str, str]]:
    """The "תחום עניין" select -> (value, Hebrew label) per category."""
    select = LexborHTMLParser(page_html).css_first("select#interests")
    options = select.css("option") if select else []
    return [(v, clean_str(o.text()) or v) for o in options if (v := o.attributes.get("value") or "")]


def parse_listing(page_html: str) -> dict[str, list[str]]:
    """Listing -> {event id: []} in page order."""
    tree = LexborHTMLParser(page_html)
    ids = (m[1] for a in tree.css("a[href^='/events/']") if (m := _ID.match(a.attributes.get("href") or "")))
    return {event_id: [] for event_id in ids}


def parse_event(
    event_id: str, url: str, page_html: str, genres: list[str], base: str, timezone: str
) -> ScrapedEvent | None:
    """Event page -> event; None without a title or a date."""
    tree = LexborHTMLParser(page_html)
    title_node = tree.css_first(".Path-1185")
    date_node = tree.css_first("[data-eventdate]")
    title = clean_str(title_node.text()) if title_node else None
    stamp = _STAMP.match((date_node.attributes.get("data-eventdate") or "") if date_node else "")
    if not title or not stamp:
        return None
    y, mo, d, h, mi = (int(g) for g in stamp.groups())
    body = next((n for n in tree.css("div") if "w-3/5" in (n.attributes.get("class") or "").split()), None)
    image = next((src for n in tree.css("img.rounded-2xl") if (src := n.attributes.get("src") or "")), None)
    address = city = None
    if maps := _MAPS.search(page_html):
        address = clean_str(parse_qs(urlsplit(html.unescape(maps.group(0))).query).get("q", [""])[0])
        city = clean_str(address.rsplit(",", 1)[-1]) if address and "," in address else None
    return ScrapedEvent(
        source=MeetyScraper.name,
        source_event_id=event_id,
        url=url,
        title=title,
        description=html_to_text(body.html) if body else None,
        start_at=datetime(y, mo, d, h, mi, tzinfo=zone(timezone)),
        address=address,
        city=city,
        source_genres=genres,
        images=[base + quote(image)] if image and image != _DEFAULT_IMAGE and image.startswith("/") else [],
    )
