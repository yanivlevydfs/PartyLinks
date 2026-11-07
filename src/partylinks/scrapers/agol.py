"""AGOL (agol-join.vercel.app) - organic & progressive house collective, Tel Aviv.

A one-page Next.js site, server-rendered: the sign-up page for the collective's next party. No API,
no JSON-LD, no sitemap (every other path redirects to /login). One request reads the page:

- ``section[aria-labelledby=ev]`` ("Next event"): the title line ("AGOL Takeover · Weekend by Art
  Club") and the English when/where line ("Fri 6.11.2026 · 00:00–06:00 · Art Club, Hazerem 9, Tel
  Aviv"), then the same in Hebrew (venue name and address used as they come).
- The flyer ``img`` (``/brand/takeover-<date>-1080.jpg``): its ``alt`` names the lineup ("Lineup:
  A, B, C.").

A night after midnight is written under the evening it belongs to (Fri 6.11, 00:00-06:00 is the
night of 6-7.11), so a start before ``night_ends_hour`` falls on the next day. No price (the party
runs a guest list the organizers approve), no ticket link. The id is the event's date, so the
listing stays one event until the page names the next one.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from urllib.parse import urljoin

from pydantic import Field
from selectolax.lexbor import LexborHTMLParser

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_WHEN = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})\s*·\s*(\d{1,2}):(\d{2})\s*[–-]\s*(\d{1,2}):(\d{2})")
_LINEUP = re.compile(r"Lineup:\s*([^.]+)")


class AgolSettings(SourceSettings):
    site_url: str = "https://agol-join.vercel.app"
    listing_path: str = "/"
    venue_name: str | None = None  # else the page's Hebrew venue line
    address: str | None = None
    city: str
    latitude: float | None = None
    longitude: float | None = None
    night_ends_hour: int = 6  # a start before this hour belongs to the night after the date written
    source_genres: list[str] = Field(default_factory=lambda: ["organic house", "progressive house"])


class AgolScraper(BaseScraper[AgolSettings]):
    name = "agol"
    settings_model = AgolSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        url = s.site_url + s.listing_path
        event = parse_page(await self.get_text(url), url, s, self.timezone)
        if event and is_current(event.start_at, event.end_at, RetentionSettings(), self.timezone):
            yield event


def parse_page(page_html: str, url: str, s: AgolSettings, timezone: str) -> ScrapedEvent | None:
    """The sign-up page -> its next event; None when the page names none."""
    tree = LexborHTMLParser(page_html)
    section = tree.css_first("section[aria-labelledby=ev]")
    if section is None:
        return None
    lines = [clean_str(p.text()) or "" for p in section.css("p")]
    when = next((m for line in lines if (m := _WHEN.search(line))), None)
    if when is None or len(lines) < 2:
        return None
    day, month, year, sh, sm, eh, em = map(int, when.groups())
    start_at = datetime(year, month, day, sh, sm, tzinfo=zone(timezone))
    if sh < s.night_ends_hour:
        start_at += timedelta(days=1)
    end_at = start_at.replace(hour=eh, minute=em)
    if end_at <= start_at:
        end_at += timedelta(days=1)
    hebrew_place = next((line for line in lines[2:] if "," in line and not _WHEN.search(line)), "")
    venue, _, address = hebrew_place.partition(",")
    flyer = tree.css_first("img[alt*=Lineup]")
    alt = (flyer.attributes.get("alt") or "") if flyer else ""
    lineup = [a.strip() for a in m[1].split(",") if a.strip()] if (m := _LINEUP.search(alt)) else []
    image = urljoin(url, flyer.attributes.get("src") or "") if flyer and flyer.attributes.get("src") else None
    return ScrapedEvent(
        source=AgolScraper.name,
        source_event_id=start_at.date().isoformat(),
        url=url,
        title=lines[0].split(" · ")[0].strip() or "AGOL",
        start_at=start_at,
        end_at=end_at,
        venue_name=s.venue_name or clean_str(venue) or None,
        address=s.address or clean_str(address.rsplit(",", 1)[0]) or None,
        city=s.city,
        latitude=s.latitude,
        longitude=s.longitude,
        source_genres=list(s.source_genres),
        lineup=lineup,
        images=[image] if image else [],
        description=lines[0] if " · " in lines[0] else None,
    )
