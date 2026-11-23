"""People & Computers (anashim umahshevim, events.co.il) - Israel's hi-tech conference organizer.

robots.txt allows everything. The conference listing ``GET /events?locale=he`` holds one card per
upcoming conference and forum meeting with its title, date and hours ("שני, 21 בדצמבר 2026 |
08:30 - 12:00"), place ("venue, street, city"), a teaser and the artwork, linking to the
conference's own site (also the id). Its count is the site's own "Upcoming Conferences" number.
``GET /feed.rss`` lists only the conferences, not the forum meetings (8 of 11, Nov 2026); an item
there that the listing lacks is still read. No prices: most are free with registration or sold to
companies.
"""

from __future__ import annotations

import html
import re
from collections.abc import AsyncIterator
from datetime import date, datetime, time
from email.utils import parsedate_to_datetime

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import ParseError, clean_str, html_to_text, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_ITEM = re.compile(r"<item>(.*?)</item>", re.DOTALL)
_CARD = re.compile(r"<div class='catalog-card-wrapper")
_MONTHS = {
    "ינואר": 1, "פברואר": 2, "מרץ": 3, "מרס": 3, "אפריל": 4, "מאי": 5, "יוני": 6,
    "יולי": 7, "אוגוסט": 8, "ספטמבר": 9, "אוקטובר": 10, "נובמבר": 11, "דצמבר": 12,
}  # fmt: skip
_DAY = re.compile(r"(\d{1,2})\s+ב?([א-ת]+)\s+(\d{4})")
_HOURS = re.compile(r"(\d{1,2}):(\d{2})(?:\s*-\s*(\d{1,2}):(\d{2}))?")
ORGANIZER = "אנשים ומחשבים"
GENRES = ["hi-tech", "conference"]


class PeopleComputersSettings(SourceSettings):
    listing_url: str
    feed_url: str


class PeopleComputersScraper(BaseScraper[PeopleComputersSettings]):
    name = "peoplecomputers"
    settings_model = PeopleComputersSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        events: dict[str, ScrapedEvent] = {}
        cards = _CARD.split(await self.get_text(self.settings.listing_url))[1:]
        items = _ITEM.findall(await self.get_text(self.settings.feed_url))
        self.log.debug("listed %d cards, %d feed items", len(cards), len(items))
        for card in cards:
            try:
                event = parse_card(card, self.timezone)
            except Exception as exc:  # noqa: BLE001 - one bad card must not cost the others
                self.record_failure(f"card {card[:80]!r}", exc)
                continue
            events.setdefault(event.source_event_id, event)
        for item in items:
            try:
                event = parse_item(item)
            except Exception as exc:  # noqa: BLE001
                self.record_failure(f"item {item[:80]!r}", exc)
                continue
            events.setdefault(event.source_event_id, event)
        for event in events.values():
            if is_current(event.start_at, event.end_at, RetentionSettings(), self.timezone):
                yield event


def _event_id(link: str) -> str:
    return re.sub(r"^https?://|/+$", "", link)


def parse_listing(page: str, tz: str) -> list[ScrapedEvent]:
    return [parse_card(card, tz) for card in _CARD.split(page)[1:]]


def parse_card(card: str, tz: str) -> ScrapedEvent:
    def text(pattern: str) -> str | None:
        found = re.search(pattern, card, re.DOTALL)
        return clean_str(html.unescape(re.sub(r"<[^>]+>", " ", found[1]))) if found else None

    badge = re.search(r'class="catalog-date-badge" aria-label="([^"]*)" href="([^"]+)"', card)
    title = text(r"catalog-card-title[^>]*>\s*<a[^>]*>(.*?)</a>")
    if not badge or not title:
        raise ParseError(f"card without date, link or title: {card[:120]!r}")
    when, link = html.unescape(badge[1]), html.unescape(badge[2])
    found = _DAY.search(when)
    if not found or found[2] not in _MONTHS:
        raise ParseError(f"unreadable date {when!r}")
    day = date(int(found[3]), _MONTHS[found[2]], int(found[1]))
    hours = _HOURS.search(when.split("|", 1)[-1]) if "|" in when else None
    start_at = datetime.combine(day, time(int(hours[1]), int(hours[2])) if hours else time(), zone(tz))
    end_at = datetime.combine(day, time(int(hours[3]), int(hours[4])), zone(tz)) if hours and hours[3] else None
    place = text(r"fa-map-marker-alt.*?</span>\s*<span>(.*?)</span>") or ""
    parts = [p.strip() for p in place.split(",") if p.strip()]
    teaser = [text(r"class='catalog-card-subtitle'>(.*?)</span>"), text(r"class='catalog-card-description[^']*'>(.*?)</p>")]
    image = re.search(r'class="catalog-image[^"]*" src="([^"]+)"', card)
    return ScrapedEvent(
        source=PeopleComputersScraper.name,
        source_event_id=_event_id(link),
        url=link,
        title=title,
        description="\n\n".join(t for t in teaser if t) or None,
        start_at=start_at,
        end_at=end_at,  # an end past midnight moves to the next day in ScrapedEvent
        time_known=hours is not None,
        venue_name=parts[0] if parts else None,
        address=", ".join(parts[1:]) or None,
        organizer=ORGANIZER,
        source_genres=GENRES,
        images=[html.unescape(image[1])] if image else [],
    )


def parse_feed(feed: str) -> list[ScrapedEvent]:
    return [parse_item(item) for item in _ITEM.findall(feed)]


def parse_item(item: str) -> ScrapedEvent:
    def tag(name: str) -> str | None:
        found = re.search(rf"<{name}>(.*?)</{name}>", item, re.DOTALL)
        return found[1].strip() if found else None

    title, link, published = clean_str(html.unescape(tag("title") or "")), tag("link"), tag("pubDate")
    if not title or not link or not published:
        raise ParseError(f"item without title, link or date: {item[:120]!r}")
    body = re.sub(r"^<!\[CDATA\[|\]\]>$", "", tag("description") or "")
    parts = [p.strip() for p in html.unescape(tag("location") or "").split(",") if p.strip()]
    return ScrapedEvent(
        source=PeopleComputersScraper.name,
        source_event_id=_event_id(link),
        url=link,
        title=title,
        description=html_to_text(body),
        start_at=parsedate_to_datetime(published),
        venue_name=parts[0] if parts else None,
        address=", ".join(parts[1:]) or None,
        organizer=ORGANIZER,
        source_genres=GENRES,
        images=re.findall(r'src="([^"]+)"', body),
    )
