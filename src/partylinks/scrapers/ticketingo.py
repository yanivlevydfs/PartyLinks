"""Ticketingo (ticketingo.co.il): the Hebrew storefront of Sportsevents365, tickets abroad. Only its
entertainment type is read: concerts, opera, ballet, theatre, musicals and festivals. Sport is not.

robots.txt allows the site and asks for a 10 s crawl delay. The page itself calls
``/api/events/type/<id>?page&perPage&dateType=1&homeAway=3`` (dateType 1 = upcoming), 100 a page
with ``links.next``: ~11 requests a run. Each row has the name, the date and local time
(``dateISO`` carries the venue's wall clock with a "+00:00" that is not UTC), the venue with
address and coordinates, the city and country in Hebrew, and the lowest price in shekels.
No artwork in the rows.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import Field
from tzfpy import get_tz

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, to_decimal, to_float
from partylinks.retention import is_current
from partylinks.scrapers.abroad import country_code, local_zone
from partylinks.scrapers.base import BaseScraper, SourceSettings

_IN_PARENS = re.compile(r"\(([^()]+)\)")


class TicketingoSettings(SourceSettings):
    site_url: str = "https://ticketingo.co.il"
    listing_path: str = "/api/events/type/{type}"
    event_path: str = "/event/{id}"
    # Event types read: 1023 is "בידור" (concerts, opera, ballet, theatre); every other type is a sport.
    types: list[int] = Field(default_factory=lambda: [1023])
    per_page: int = Field(100, ge=1, le=100)
    max_pages: int = Field(50, ge=1)
    unknown_venues: list[str] = Field(default_factory=lambda: ["To be decided", "TBD", "TBA"])


class TicketingoScraper(BaseScraper[TicketingoSettings]):
    name = "ticketingo"
    settings_model = TicketingoSettings
    abroad_only = True

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        retention = RetentionSettings()
        for kind in s.types:
            for page in range(1, s.max_pages + 1):
                params = {"page": page, "perPage": s.per_page, "dateType": 1, "homeAway": 3}
                data = await self.get_json(s.site_url + s.listing_path.format(type=kind), params=params, expect=dict)
                for row in data.get("data") or []:
                    try:
                        event = parse_event(row, s, self.timezone)
                    except Exception as exc:  # noqa: BLE001 - one bad row must not cost the others
                        self.record_failure(f"event {row.get('id')}", exc)
                        continue
                    if event and is_current(
                        event.start_at, event.end_at, retention, event.time_zone or self.timezone,
                        time_known=event.time_known,
                    ):  # fmt: skip
                        yield event
                if not (data.get("links") or {}).get("next"):
                    break


def _country(name: str | None) -> str | None:
    """ISO code of a Hebrew country name; "ארה"ב (ארצות הברית)" by its name in brackets."""
    if not name:
        return None
    for candidate in (name, *_IN_PARENS.findall(name), _IN_PARENS.sub("", name).strip()):
        code = country_code(candidate)
        if code and code != "ZZ":
            return code
    return None


def parse_event(row: dict[str, Any], s: TicketingoSettings, timezone: str) -> ScrapedEvent | None:
    title, when = clean_str(row.get("name")), clean_str(row.get("dateISO"))
    if not row.get("id") or not title or not when or not row.get("finalDate", True):
        return None
    venue = row.get("venue") if isinstance(row.get("venue"), dict) else {}
    city = clean_str((row.get("city") or {}).get("name"))
    country = _country(clean_str((row.get("country") or {}).get("name"))) or "ZZ"
    lat, lng = to_float(venue.get("latitude")), to_float(venue.get("longitude"))
    # The venue's own zone by its coordinates (a US country default would put Florida in Chicago's).
    zone_name = (get_tz(lng, lat) if lat and lng else None) or local_zone(
        country, timezone, (city or "").split(",")[-1].strip() or None
    )
    # The wall clock at the venue: the "+00:00" the API adds is not UTC.
    start = datetime.fromisoformat(when).replace(tzinfo=ZoneInfo(zone_name))
    price = to_decimal((row.get("minTicketPrice") or {}).get("price"))
    tournament = clean_str((row.get("tournament") or {}).get("name"))
    url = s.site_url + s.event_path.format(id=row["id"])
    return ScrapedEvent(
        source=TicketingoScraper.name,
        source_event_id=str(row["id"]),
        url=url,
        title=title,
        start_at=start,
        time_known=bool(row.get("finalTime")),
        venue_name=None if (name := clean_str(venue.get("name"))) in s.unknown_venues else name,
        address=" ".join((clean_str(venue.get("address")) or "").split()) or None,
        city=city,
        country=country,
        time_zone=zone_name,
        latitude=lat if lat and lng else None,
        longitude=lng if lat and lng else None,
        source_genres=[tournament.lower()] if tournament else [],  # the type, "בידור", is every row's
        lineup=[n for p in row.get("participants") or [] if isinstance(p, dict) and (n := clean_str(p.get("name")))],
        currency="ILS",
        min_price=price,
        extra={"purchase_url": url},
    )
