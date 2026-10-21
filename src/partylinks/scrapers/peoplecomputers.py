"""People & Computers (anashim umahshevim, events.co.il) - Israel's hi-tech conference organizer.

robots.txt allows everything. Listing and details in one ``GET /feed.rss``: one ``<item>`` per
upcoming conference with title, ``pubDate`` (the conference's start, with UTC offset),
``location`` ("venue, street, city"), a description holding the artwork and a one-line teaser,
and the conference's own site as ``link`` (also the id). No prices: most are free with
registration or sold to companies.
"""

from __future__ import annotations

import html
import re
from collections.abc import AsyncIterator
from email.utils import parsedate_to_datetime

from partylinks.models import ScrapedEvent
from partylinks.parsing import ParseError, clean_str, html_to_text
from partylinks.scrapers.base import BaseScraper, SourceSettings

_ITEM = re.compile(r"<item>(.*?)</item>", re.DOTALL)


class PeopleComputersSettings(SourceSettings):
    feed_url: str


class PeopleComputersScraper(BaseScraper[PeopleComputersSettings]):
    name = "peoplecomputers"
    settings_model = PeopleComputersSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        events = parse_feed(await self.get_text(self.settings.feed_url))
        self.log.info("listed %d conferences", len(events))
        for event in events:
            yield event


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
        source_event_id=re.sub(r"^https?://|/+$", "", link),
        url=link,
        title=title,
        description=html_to_text(body),
        start_at=parsedate_to_datetime(published),
        venue_name=parts[0] if parts else None,
        address=", ".join(parts[1:]) or None,
        organizer="אנשים ומחשבים",
        source_genres=["hi-tech", "conference"],
        images=re.findall(r'src="([^"]+)"', body),
    )
