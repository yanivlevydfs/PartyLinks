"""viagogo (viagogo.com/il) - international ticket resale; events taking place in Israel.

Event pages answer non-browser clients with a 403 captcha page, but the explore page's own
JSON feed is public (not disallowed by robots.txt):
``GET /explore?method=getExploreEvents&lat=<b64>&lon=<b64>&radius=<metres>&page=<n>``,
latitude/longitude base64-encoded as the site's script sends them. It returns 20 events per
page sorted by date with ``total``/``remaining`` counts (a page may come back one short, so
paging follows ``total``). Each row has the name, the event page URL, venue name and
"City, [Region,] Country", category id and artwork, but the date only as ``"Oct 24"`` +
``"8:50 PM"`` + weekday - the year is the next one whose weekday matches. No prices or
addresses. The circle around Israel also catches neighbouring countries: only rows whose
location ends in ", Israel" are kept. The feed is always English; URLs are rewritten to the
Hebrew site (``/il``).
"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import Field

from partylinks.models import ScrapedEvent
from partylinks.parsing import ParseError, clean_str
from partylinks.scrapers.base import BaseScraper, SourceSettings

PAGE_SIZE = 20
COUNTRY = "Israel"


class ViagogoSettings(SourceSettings):
    site_url: str
    explore_path: str
    locale_path: str = ""  # inserted before event paths, e.g. "/il" for the Hebrew site
    latitude: float
    longitude: float
    radius_m: int = Field(..., ge=1000)
    max_pages: int = Field(50, ge=1)


class ViagogoScraper(BaseScraper[ViagogoSettings]):
    name = "viagogo"
    settings_model = ViagogoSettings

    def default_headers(self) -> dict[str, str]:
        return {"Accept": "application/json", "Referer": self.settings.site_url + "/explore"}

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        rows = await self.paginate(self._page, PAGE_SIZE, self.settings.max_pages)
        self.log.info("listed %d events", len(rows))
        today = datetime.now(ZoneInfo(self.timezone)).date()
        seen: set[str] = set()
        for row in rows:
            try:
                event = parse_event(row, self.settings, self.timezone, today)
            except Exception as exc:  # noqa: BLE001 - one bad row never drops the rest
                self.record_failure(f"event {row.get('eventId') if isinstance(row, dict) else row!r}", exc)
                continue
            if event and event.source_event_id not in seen:
                seen.add(event.source_event_id)
                yield event

    async def _page(self, index: int) -> tuple[list[dict[str, Any]], Any]:
        s = self.settings
        params = {
            "method": "getExploreEvents",
            "lat": _b64(s.latitude),
            "lon": _b64(s.longitude),
            "radius": s.radius_m,
            "page": index,
        }
        payload = await self.get_json(s.site_url + s.explore_path, expect=dict, params=params)
        events = payload.get("events")
        if not isinstance(events, list):
            raise ParseError("explore feed without an events list")
        return events, payload.get("total")


def _b64(value: float) -> str:
    return base64.b64encode(str(value).encode()).decode()


def infer_date(month_day: str, weekday: str | None, today: date) -> date | None:
    """``"Oct 24"`` -> the next such date (from yesterday on) whose weekday is ``"Sat"``;
    without a matching weekday, simply the next such date."""
    candidates = []
    for year in range(today.year, today.year + 3):
        try:
            day = datetime.strptime(f"{month_day} {year}", "%b %d %Y").date()
        except ValueError:
            continue  # bad text, or Feb 29 in a non-leap year
        if day >= today - timedelta(days=1):
            candidates.append(day)
    matching = [d for d in candidates if weekday and d.strftime("%a") == weekday[:3]]
    return (matching or candidates or [None])[0]


def parse_event(row: dict[str, Any], s: ViagogoSettings, timezone: str, today: date) -> ScrapedEvent | None:
    """One feed row; None for an event outside Israel."""
    event_id, title = clean_str(row.get("eventId")), clean_str(row.get("name"))
    url = clean_str(row.get("url"))
    if not event_id or not title or not url:
        raise ParseError("eventId/name/url missing")
    location = [p.strip() for p in (clean_str(row.get("formattedVenueLocation")) or "").split(",")]
    if location[-1] != COUNTRY:
        return None
    if s.locale_path and url.startswith(s.site_url + "/") and not url.startswith(s.site_url + s.locale_path + "/"):
        url = s.site_url + s.locale_path + url[len(s.site_url) :]
    start = None
    day = (
        None
        if row.get("isTbd")
        else infer_date(str(row.get("formattedDateWithoutYear") or ""), row.get("dayOfWeek"), today)
    )
    if day:
        try:
            clock = datetime.strptime(str(row.get("formattedTime") or ""), "%I:%M %p").time()
        except ValueError:
            clock = datetime.min.time()
        start = datetime.combine(day, clock, ZoneInfo(timezone))
    # /<Category>-Tickets/<Genre>/.../E-<id>: e.g. Concert-Tickets / Dance-and-Electronic.
    path = url.split("://", 1)[-1].split("/")[1:]
    path = path[1:] if s.locale_path and path and "/" + path[0] == s.locale_path else path
    category = path[0].removesuffix("-Tickets").replace("-", " ") if len(path) > 1 else None
    genre = path[1].replace("-", " ") if len(path) > 2 and not path[1].endswith("-Tickets") else None
    image = clean_str(row.get("imageUrl"))
    return ScrapedEvent(
        source=ViagogoScraper.name,
        source_event_id=event_id,
        url=url,
        title=title,
        start_at=start,
        venue_name=clean_str(row.get("venueName")),
        city=location[0] if len(location) > 1 else None,
        source_genres=[genre] if genre else [],
        event_types=[category] if category else [],
        images=[image] if image else [],
        extra={
            "venue_id": clean_str(row.get("venueId")),
            "category_id": clean_str(row.get("categoryId")),
            "time_confirmed": bool(row.get("isTimeConfirmed")),
            "has_listings": bool(row.get("hasActiveListings")),
            "resale": True,
        },
    )
