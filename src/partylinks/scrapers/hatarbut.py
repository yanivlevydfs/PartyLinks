"""Heichal HaTarbut Tel Aviv (hatarbut.co.il) - the Charles Bronfman Auditorium's show calendar.

WordPress + Event Organiser; the site exposes no events REST route. The calendar page
(``/calendar``) fills itself from ``admin-ajax.php?action=eventorganiser-fullcal`` (robots.txt
allows admin-ajax): one JSON row per performance with title, page URL, local start / end and a
truncated description that names the hall ("אולם: צוקר"). A multi-day run comes as all-day
rows, one per day, whose time is in the description's date list. ``category`` narrows it
server-side to one site category (``הופעות-ומוזיקה``, which almost every show carries). Prices are not listed;
each show page has a "לרכישת כרטיסים" button to the real seller (Eventer, Ticketmaster, Kupat,
Tickchak...), kept as ``purchase_url`` so dedup merges this listing with the seller's, and an
``og:image``. One page request per show, not per performance.
"""

from __future__ import annotations

import hashlib
import html
import re
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any

from pydantic import Field

from partylinks.models import ScrapedEvent
from partylinks.parsing import ParseError, clean_str, html_to_text, og_image, parse_iso, zone
from partylinks.scrapers.base import BaseScraper, SourceSettings

# The Elementor button whose label is "לרכישת כרטיסים" (buy tickets).
_BUY = re.compile(
    r'<a class="elementor-button[^"]*" href="(https?://[^"]+)"[^>]*>(?:(?!</a>).)*?לרכישת כרטיסים', re.DOTALL
)
# A run's dates and times, listed in the description: "07.12.26 | יום ב' | 17:00".
_SHOW_TIME = re.compile(r"(\d{2})\.(\d{2})\.(\d{2})\s*\|[^|]*\|\s*(\d{1,2}):(\d{2})")
_HALL = re.compile(r"אולם:\s*(?:אולם\s+)?(.+?)\s+(?:תחילת|לרכישת)")
# The description opens with "<date> <times></br></br>" and the site's door/membership boilerplate,
# which ends at "...על בסיס מקום פנוי" or "...אינם מסומנים."; the show's own text follows.
_BOILERPLATE_END = re.compile(r"מקום פנוי?|אינם מסומנים\.?")


class HatarbutSettings(SourceSettings):
    site_url: str
    ajax_path: str = "/wp-admin/admin-ajax.php"
    category: str | None = None  # site category slug; None = the whole calendar
    days_ahead: int = Field(400, ge=1)
    venue_name: str
    address: str | None = None
    city: str | None = None


class HatarbutScraper(BaseScraper[HatarbutSettings]):
    name = "hatarbut"
    settings_model = HatarbutSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        today = datetime.now(zone(self.timezone)).date()
        params = {"action": "eventorganiser-fullcal", "start": today.isoformat(),
                  "end": (today + timedelta(days=s.days_ahead)).isoformat()}  # fmt: skip
        if s.category:
            params["category"] = s.category
        rows = [
            r for r in await self.get_json(s.site_url + s.ajax_path, expect=list, params=params) if isinstance(r, dict)
        ]
        by_page: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            by_page.setdefault(str(row.get("url") or ""), []).append(row)
        self.log.info("listed %d performances of %d shows", len(rows), len(by_page))

        async def show(url: str) -> list[ScrapedEvent]:
            page = await self.optional(f"page {url}", self.get_text(url)) if url else None
            events = []
            for row in by_page[url]:
                try:
                    events.append(parse_row(row, page, s, self.name, self.timezone))
                except (ParseError, ValueError) as exc:
                    self.record_failure(url or str(row.get("title")), exc)
            return events

        async for events in self.stream_isolated(by_page, show, label=lambda u: u):
            for event in events:
                yield event


def parse_row(row: dict[str, Any], page: str | None, s: HatarbutSettings, source: str, timezone: str) -> ScrapedEvent:
    """One performance of the calendar JSON; ``page`` is its show page (seller link, artwork) if fetched."""
    url, start = clean_str(row.get("url")), parse_iso(row.get("start"), timezone)
    if not url or not start:
        raise ParseError("calendar row without url or start")
    raw = str(row.get("description") or "")
    end = parse_iso(row.get("end"), timezone)
    if row.get("allDay"):  # one day of a run: the time is in the description's date list, else unknown
        end = None
        for d, m, y, hh, mm in _SHOW_TIME.findall(raw):
            if (2000 + int(y), int(m), int(d)) == (start.year, start.month, start.day):
                start = start.replace(hour=int(hh), minute=int(mm))
    hall = _HALL.search(raw)
    body = raw.partition("</br></br>")[2] or raw
    description = html_to_text(_BOILERPLATE_END.split(body, 1)[-1])
    buy = _BUY.search(page) if page else None
    image = og_image(page) if page else None
    extra = {
        "purchase_url": html.unescape(buy.group(1)) if buy else None,
        "hall": hall.group(1).strip() if hall else None,
    }
    return ScrapedEvent(
        source=source,
        # A show page can hold several performances: page + day identifies one.
        source_event_id=hashlib.sha1(f"{url}|{start.date()}".encode()).hexdigest()[:16],
        url=url,
        title=html.unescape(clean_str(row.get("title")) or ""),
        source_genres=["הופעות ומוזיקה"],  # the calendar category read (series slugs are not genres)
        description=description,
        start_at=start,
        end_at=end if end and end > start else None,
        venue_name=s.venue_name,
        address=s.address,
        city=s.city,
        images=[image] if image else [],
        extra={k: v for k, v in extra.items() if v},
    )
