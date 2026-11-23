"""The World Centre for North African Jewish Heritage (mjc-jerusalem.com, "המרכז העולמי למורשת יהדות צפון אפריקה"),
Jerusalem - its museum's evenings: piyut and song nights, lectures, book launches, exhibition openings, study
days, hilulot and memorial evenings.

The site is a Webydo blog. ``/events`` (one request) lists every post, newest first (~100, most past): per card
(``article.blog-card``) the title (``data-title``), a one-line summary with the day and time ("יום חמישי,
3.12.2026, בשעה 19:00", ``data-excerpt``), the artwork and the post page. A post whose summary names a coming day
is read (one request each) for its text (``.blog-post__content``), which repeats the day, time and place. Classes,
courses, women's groups, school programmes and exhibitions open on weekdays (a summary without one day) are no
events and have no single day, so they are left out. The place is the centre (venue, address and point from the
config) unless the text names a synagogue with its street ("בבית הכנסת ״צוף דבש״, רחוב פלוגות הכותל 15"),
which is then the venue (geocoded). Registration is by phone: the post is the link. robots.txt allows all but
/api/ and /temp/.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date, datetime, time
from html import unescape
from urllib.parse import urljoin

from pydantic import Field
from selectolax.lexbor import LexborHTMLParser

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, html_to_text, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.text_prices import text_tickets

_DAY = re.compile(r"(?<![\d.])(\d{1,2})\.(\d{1,2})\.(20\d{2}|\d{2})(?![\d])")
_CLOCK = re.compile(r"(?<![\d.:])([01]?\d|2[0-3]):([0-5]\d)(?![\d:])")
_SYNAGOGUE = re.compile(
    r"(בית הכנסת\s*[״\"][^״\"]+[״\"])\s*,\s*(רחוב\s+[^,.]+?\s+\d+[א-ת]?)\s*,(?:[^,.]{0,30},)?\s*ירושלים"
)


class MjcSettings(SourceSettings):
    site_url: str = "https://mjc-jerusalem.com"
    listing_path: str = "/events"
    # Words of a post that is no one-day public event (classes, groups, school programmes, an exhibition's hours).
    skip_words: list[str] = Field(
        default_factory=lambda: ["קורס", "חוג", "קבוצת", "תכניות חינוכיות", 'גפ"ן', "גפ״ן", "קול קורא", "ימים א׳"]
    )
    venue_name: str
    address: str
    city: str
    latitude: float | None = None
    longitude: float | None = None


@dataclass
class Card:
    url: str
    title: str
    summary: str
    image: str | None
    day: date
    clock: time | None


class MjcScraper(BaseScraper[MjcSettings]):
    name = "mjc"
    settings_model = MjcSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        tz = zone(self.timezone)
        cards = parse_listing(await self.get_text(s.site_url + s.listing_path), s)
        coming = [
            c
            for c in cards
            if is_current(
                datetime.combine(c.day, c.clock or time.min, tzinfo=tz),
                None,
                RetentionSettings(),
                self.timezone,
                time_known=c.clock is not None,
            )
        ]
        coming.sort(key=lambda c: (c.day, c.clock or time.min))  # nearest first
        self.log.debug("%d posts with one day, %d coming", len(cards), len(coming))
        for card in coming:
            html = await self.optional(f"post {card.url}", self.get_text(card.url))
            event = build_event(card, post_text(html) if html else None, s, self.timezone)
            if is_current(
                event.start_at, event.end_at, RetentionSettings(), self.timezone, time_known=event.time_known
            ):
                yield event


def parse_listing(html: str, s: MjcSettings) -> list[Card]:
    """The posts whose summary names one day (a class, a group or a weekday exhibition is skipped)."""
    tree = LexborHTMLParser(html)
    cards: list[Card] = []
    for node in tree.css("article.blog-card"):
        title = clean_str(unescape(node.attributes.get("data-title") or ""))
        summary = clean_str(unescape(node.attributes.get("data-excerpt") or "")) or ""
        link = node.css_first("a[href]")
        href = (link.attributes.get("href") or "").strip() if link is not None else ""
        if not title or not href:
            continue
        text = f"{title} {summary}"
        if any(w in text for w in s.skip_words):
            continue
        days = {d for d in (_date(m) for m in _DAY.finditer(summary)) if d}
        if len(days) != 1:  # no day, or a range of days ("1.8.26-1.10.26"): no one-day event
            continue
        clock = _CLOCK.search(summary)
        image = node.css_first("img[src]")
        src = (image.attributes.get("src") or "").strip() if image is not None else ""
        cards.append(
            Card(
                url=urljoin(s.site_url + "/", href),
                title=title,
                summary=summary,
                image=src if src.startswith("http") else None,
                day=days.pop(),
                clock=time(int(clock.group(1)), int(clock.group(2))) if clock else None,
            )
        )
    return cards


def post_text(html: str) -> str | None:
    node = LexborHTMLParser(html).css_first(".blog-post__content")
    return html_to_text(node.html) if node is not None and node.html else None


def build_event(card: Card, text: str | None, s: MjcSettings, timezone: str) -> ScrapedEvent:
    """A card and its post's text -> the event."""
    tz = zone(timezone)
    clock = card.clock
    if clock is None and text:  # the post's "בשעה 19:00", else its programme's first clock ("14:30 — יציאה")
        found = re.search(r"בשעה\s+([01]?\d|2[0-3]):([0-5]\d)", text) or _CLOCK.search(text)
        if found:
            clock = time(int(found.group(1)), int(found.group(2)))
    body = text or card.summary
    venue, address, lat, lng = s.venue_name, s.address, s.latitude, s.longitude
    if place := _SYNAGOGUE.search(body):
        venue, address, lat, lng = place.group(1), place.group(2), None, None
    tickets = text_tickets(body)
    prices = [t.price for t in tickets if t.price is not None]
    return ScrapedEvent(
        source=MjcScraper.name,
        source_event_id=card.url.rstrip("/").rsplit("/", 1)[-1],
        url=card.url,
        title=card.title,
        description=text or card.summary,
        start_at=datetime.combine(card.day, clock or time.min, tzinfo=tz),
        time_known=clock is not None,
        venue_name=venue,
        address=address,
        city=s.city,
        latitude=lat,
        longitude=lng,
        source_genres=[],
        organizer=s.venue_name,
        images=[card.image] if card.image else [],
        tickets=tickets,
        min_price=min(prices, default=None),
        max_price=max(prices, default=None),
        extra={"purchase_url": card.url},
    )


def _date(match: re.Match[str]) -> date | None:
    year = int(match.group(3))
    try:
        return date(year + 2000 if year < 100 else year, int(match.group(2)), int(match.group(1)))
    except ValueError:
        return None
