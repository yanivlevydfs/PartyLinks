"""Jaffa Theatre (jaffatheatre.org.il) - the Arab-Hebrew stage at Mifratz Shlomo, Jaffa: plays in
Arabic and Hebrew, Arab music concerts and its music festival.

The theatre's home page links every upcoming performance to its TicketTool box office
(``new1.tickettool.net/web/event/event_id/<id>``, ~40). TicketTool has no API or JSON-LD; each
performance's page title holds the show and its date: "<theatre> | ...: <show> - 3 דצמבר 2026 20:30".
The artwork is ``/images/event/<id>.jpg`` there. One venue: its place comes from the config. No prices
in the pages. robots.txt of both sites allows these pages.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import datetime

from pydantic import Field

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import ParseError, clean_str, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_MONTHS = {
    m: i
    for i, m in enumerate(
        ["ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני", "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר"], 1
    )
}
_WHEN = re.compile(
    r"^(?P<title>.+?)\s+-\s+(?P<day>\d{1,2})\s+(?P<month>\S+)\s+(?P<year>\d{4})\s+(?P<h>\d{1,2}):(?P<m>\d{2})\s*$"
)
_TITLE = re.compile(r"<title>(.*?)</title>", re.DOTALL)


class JaffaTheatreSettings(SourceSettings):
    site_url: str = "https://www.jaffatheatre.org.il"
    listing_path: str = "/"
    box_office_url: str = "https://new1.tickettool.net"
    event_path: str = "/web/event/event_id/{id}"
    image_path: str = "/images/event/{id}.jpg"
    # The page title's prefix before the show ("תיאטרון יפו | במה לתרבות ערבית-עברית: ").
    title_prefix_separator: str = ": "
    venue_name: str
    address: str
    city: str
    latitude: float | None = None
    longitude: float | None = None
    source_genres: list[str] = Field(default_factory=list)
    # The site's section of the show page each performance links ("/libi-bamizrach/<slug>/"): its genres.
    section_genres: dict[str, list[str]] = Field(default_factory=dict)


class JaffaTheatreScraper(BaseScraper[JaffaTheatreSettings]):
    name = "jaffatheatre"
    settings_model = JaffaTheatreSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        sections = event_sections(await self.get_text(s.site_url + s.listing_path), s.site_url)
        self.log.debug("listed %d performances", len(sections))

        async def read(event_id: str) -> ScrapedEvent | None:
            url = s.box_office_url + s.event_path.format(id=event_id)
            event = parse_page(await self.get_text(url), event_id, url, s, self.timezone)
            event.source_genres = [*event.source_genres, *s.section_genres.get(sections[event_id] or "", [])]
            return event if is_current(event.start_at, event.end_at, RetentionSettings(), self.timezone) else None

        async for event in self.stream_isolated(list(sections), read, lambda i: f"performance {i}"):
            yield event


def event_sections(page_html: str, site_url: str) -> dict[str, str | None]:
    """Every TicketTool performance id the home page links (once, in page order) -> the section of the
    show page linked just before it ("shows", "libi-bamizrach"...), None when there is none."""
    links = re.compile(
        rf"{re.escape(site_url)}/(?:(?:ar|en)/)?([a-z][a-z0-9-]*)/[^\"'/]+/|tickettool\.net/web/event/event_id/(\d+)"
    )
    found: dict[str, str | None] = {}
    section = None
    for m in links.finditer(page_html):
        if m.group(1):
            section = m.group(1)
        elif m.group(2) not in found:
            found[m.group(2)] = section
    return found


def parse_page(page_html: str, event_id: str, url: str, s: JaffaTheatreSettings, timezone: str) -> ScrapedEvent:
    """A TicketTool performance page -> event, from its title "<theatre>: <show> - 3 דצמבר 2026 20:30"."""
    raw = clean_str(m.group(1)) if (m := _TITLE.search(page_html)) else None
    show = (raw or "").split(s.title_prefix_separator, 1)[-1]
    when = _WHEN.match(show)
    if not when or when["month"] not in _MONTHS:
        raise ParseError(f"no show and date in the title: {raw!r}")
    start_at = datetime(
        int(when["year"]),
        _MONTHS[when["month"]],
        int(when["day"]),
        int(when["h"]),
        int(when["m"]),
        tzinfo=zone(timezone),
    )
    return ScrapedEvent(
        source=JaffaTheatreScraper.name,
        source_event_id=event_id,
        url=url,
        title=clean_str(when["title"]) or "",
        start_at=start_at,
        venue_name=s.venue_name,
        address=s.address,
        city=s.city,
        latitude=s.latitude,
        longitude=s.longitude,
        source_genres=list(s.source_genres),
        images=[s.box_office_url + s.image_path.format(id=event_id)],
        extra={"purchase_url": url},
    )
