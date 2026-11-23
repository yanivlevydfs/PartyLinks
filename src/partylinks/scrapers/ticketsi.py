"""Ticketsi (ticketsi.co.il, "טיקטסי") - a magazine of show guides (theatre, concerts, stand-up, kids' shows,
lectures, opera, dance) whose tickets sell on its Kupat Bravo white label (2005.kartisim.co.il).

The site is its own PHP. Its show calendars ("לוח ההופעות", and one per category: ``pages``, page -> the
site's category; the general calendar and "today" list without one) carry each show as a schema.org ``Event``
(JSON-LD: name, the run's first and last day, city, artwork, prices) whose offer links the white label's
``/announce/<show id>``; ~15 pages, ~580 shows. The white label's robots.txt disallows everything, so each
show's performances are read from the same show id on kupatbravo.co.il (same platform; robots.txt allows
``/announce/``): its microdata gives each performance's start, hall, street, city, coordinates, prices and
tickets left (``kupatbravo.parse_show``). One request per current show, nearest first (by its run's first day;
a run over before today is skipped). Each event links to the white-label show page and its seance's buy page
(the purchase link), which dedup keys to the same seance as `kupatbravo`. The magazine's article pages
(``/theater/<slug>``) repeat the same performances: not read.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from urllib.parse import quote

from pydantic import Field

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.kupatbravo import parse_show

_JSONLD = re.compile(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", re.DOTALL)
_ISO_DAY = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


class TicketsiSettings(SourceSettings):
    site_url: str = "https://www.ticketsi.co.il"
    # Calendar page (its path) -> the site's category for its shows ("" for the general calendars).
    pages: dict[str, str] = Field(default_factory=dict)
    # The show's id in the calendars' links (the white label's announce page).
    show_link: str = r"kartisim\.co\.il(?:\\?/)+announce(?:\\?/)+(\d+)"
    details_url: str = "https://kupatbravo.co.il/announce/{id}"
    event_url: str = "https://2005.kartisim.co.il/announce/{id}"
    buy_url: str = "https://2005.kartisim.co.il/announce/buy/{seance}"
    max_shows: int = Field(2000, ge=1)


@dataclass
class Show:
    show_id: str
    first: date | None = None
    last: date | None = None
    categories: list[str] = field(default_factory=list)


class TicketsiScraper(BaseScraper[TicketsiSettings]):
    name = "ticketsi"
    settings_model = TicketsiSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        tz = zone(self.timezone)
        today = datetime.now(tz).date()
        shows: dict[str, Show] = {}
        for path, category in s.pages.items():
            text = await self.optional(f"page {path}", self.get_text(f"{s.site_url}/{quote(path)}"))
            if text is None:
                continue
            for show in parse_calendar(text, s.show_link):
                merged = shows.setdefault(show.show_id, Show(show.show_id))
                merged.first = min(filter(None, (merged.first, show.first)), default=None)
                merged.last = max(filter(None, (merged.last, show.last)), default=None)
                if category and category not in merged.categories:
                    merged.categories.append(category)
        current = [c for c in shows.values() if c.last is None or c.last >= today]
        current.sort(key=lambda c: (c.first or date.max, c.show_id))  # nearest first
        self.log.debug("listed %d shows, %d current", len(shows), len(current))
        if len(current) > s.max_shows:
            self.log.warning("listing truncated at max_shows=%d (%d found)", s.max_shows, len(current))

        async def performances(show: Show) -> list[ScrapedEvent]:
            url = s.details_url.format(id=show.show_id)
            return relabel(parse_show(await self.get_text(url), url, self.timezone), show, s)

        async for events in self.stream_isolated(current[: s.max_shows], performances, lambda c: f"show {c.show_id}"):
            for event in sorted(events, key=lambda e: e.start_at or datetime.max.replace(tzinfo=tz)):
                if is_current(event.start_at, event.end_at, RetentionSettings(), self.timezone):
                    yield event


def parse_calendar(page_html: str, show_link: str) -> list[Show]:
    """A calendar page's shows: the id, and the run's first / last day from its JSON-LD ``Event``; a show only
    linked (no ``Event``) comes without dates."""
    link = re.compile(show_link)
    shows: dict[str, Show] = {}
    for block in _JSONLD.findall(page_html):
        try:
            node = json.loads(block)
        except ValueError:
            continue
        for item in node if isinstance(node, list) else [node]:
            if not isinstance(item, dict) or item.get("@type") != "Event":
                continue
            offers = item.get("offers") if isinstance(item.get("offers"), dict) else {}
            found = link.search(str(offers.get("url") or ""))
            if not found:
                continue
            show = shows.setdefault(found.group(1), Show(found.group(1)))
            first, last = _day(item.get("startDate")), _day(item.get("endDate"))
            show.first = min(filter(None, (show.first, first)), default=None)
            show.last = max(filter(None, (show.last, last, first)), default=None)
    for found in link.finditer(page_html):
        shows.setdefault(found.group(1), Show(found.group(1)))
    return list(shows.values())


def relabel(events: list[ScrapedEvent], show: Show, s: TicketsiSettings) -> list[ScrapedEvent]:
    """Kupat Bravo's reading of the show, as Ticketsi sells it: its categories, its white-label links."""
    out = []
    for event in events:
        genres = list(dict.fromkeys([*(g.lower() for g in show.categories), *event.source_genres]))
        extra: dict[str, Any] = {**event.extra, "purchase_url": s.buy_url.format(seance=event.source_event_id)}
        update = {
            "source": TicketsiScraper.name,
            "url": s.event_url.format(id=show.show_id),
            "source_genres": genres,
            "extra": extra,
        }
        out.append(ScrapedEvent.model_validate({**event.model_dump(), **update}))
    return out


def _day(value: Any) -> date | None:
    match = _ISO_DAY.match(str(value or ""))
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None
