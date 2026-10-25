"""Poalim Atarim (poalimatarim.com) - Bank Hapoalim's free entry to ~50 museums, parks and heritage sites.

A seasonal campaign (holiday weekends) run as a Tickchak LIVE hub: the homepage's ``__NEXT_DATA__``
holds every site under ``initialState.hub.events`` (venue, image, region category, the
``tickchak.co.il/<eid>`` registration link); ``hub.design.eventsRemovedFromPage`` lists sites the
hub hides. The hub has no dates (``date`` is null and the JSON-LD ``startDate`` is the request time):
each site's Tickchak page states them in free text under "ימי ושעות הפעילות" as
``יום שישי 8.5 - 10:00-14:00``, with no year; the address is the first line under "דרכי הגעה" and the
coordinates are in the Waze link. One event is yielded per site and opening day. Entry is free with
registration; the JSON-LD ``offers`` (all 0 ILS) name the ticket types when the site has several.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from selectolax.lexbor import LexborHTMLParser

from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import ParseError, clean_str, html_to_text, jsonld_nodes, next_data, schema_types, to_decimal
from partylinks.scrapers.base import BaseScraper, SourceSettings

# "יום שישי 8.5 - 10:00-14:00" (day.month, opening hours).
_DAY = re.compile(r"(\d{1,2})\.(\d{1,2})\s*-\s*(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})")
# A ticket type for one day only: "... בחסות בנק הפועלים - יום שישי 8.5".
_TIER_DAY = re.compile(r"(?<![\d.])(\d{1,2}\.\d{1,2})(?![\d.])")
_WAZE = re.compile(r"waze\.com/ul\?ll=(-?\d+\.\d+)%2C(-?\d+\.\d+)")
# Location "המוזיאון הימי הלאומי - חיפה", "בית המורשת הדרוזית, ג'וליס": the last part is the town.
# Not the title: "טכנודע - המוזיאון הישראלי..." puts the name after the dash.
_TOWN = re.compile(r"(?:\s*-\s+|\s+-\s*|,\s*)([^,()–-]+?)\s*$")


class PoalimAtarimSettings(SourceSettings):
    site_url: str
    event_url_template: str  # the site's Tickchak page, by {eid}
    details_selector: str
    hours_heading: str
    directions_heading: str
    organizer: str


class PoalimAtarimScraper(BaseScraper[PoalimAtarimSettings]):
    name = "poalimatarim"
    settings_model = PoalimAtarimSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        sites = parse_listing(next_data(await self.get_text(self.settings.site_url)))
        self.log.info("listed %d sites", len(sites))
        async for events in self.stream_isolated(sites, self._fetch_site, label=lambda site: str(site.get("eid"))):
            for event in events:
                yield event

    async def _fetch_site(self, site: dict[str, Any]) -> list[ScrapedEvent]:
        page = await self.get_text(self.settings.event_url_template.format(eid=site["eid"]))
        return parse_site(page, site, self.settings, self.timezone, datetime.now(ZoneInfo(self.timezone)).date())


def parse_listing(data: dict[str, Any]) -> list[dict[str, Any]]:
    hub = data.get("props", {}).get("pageProps", {}).get("initialState", {}).get("hub") or {}
    events = hub.get("events")
    if not isinstance(events, dict):
        raise ParseError("hub.events missing")
    hidden = set((hub.get("design") or {}).get("eventsRemovedFromPage") or [])
    return [e for e in events.values() if isinstance(e, dict) and e.get("eid") and e["eid"] not in hidden]


def opening_year(day: date, today: date) -> int:
    """The pages give day.month only: this year, or next year for a date just past New Year
    (a December run reading January dates). Past dates stay past and are dropped.

    ponytail: a stale page from the last campaign reads as next year's within 90 days of its dates;
    upgrade path: a year from the hub if Tickchak ever exposes one.
    """
    if day < today and day.replace(year=day.year + 1) <= today + timedelta(days=90):
        return day.year + 1
    return day.year


def _section(text: str, heading: str) -> list[str]:
    """Lines under ``heading`` up to the next blank line."""
    _, found, rest = text.partition(heading)
    return rest.strip().split("\n\n", 1)[0].splitlines() if found else []


def parse_site(
    page_html: str, site: dict[str, Any], s: PoalimAtarimSettings, timezone: str, today: date
) -> list[ScrapedEvent]:
    eid = str(site["eid"])
    box = LexborHTMLParser(page_html).css_first(s.details_selector)
    text = (html_to_text(box.html) if box else None) or ""
    tz = ZoneInfo(timezone)

    title = clean_str(site.get("title"))
    if not title:
        raise ParseError("site title missing")
    venue = clean_str((site.get("venue") or {}).get("title")) or clean_str(site.get("location")) or title
    town = _TOWN.search(clean_str(site.get("location")) or "")
    address = next(iter(_section(text, s.directions_heading)), None)
    waze = _WAZE.search(page_html)
    image = clean_str((site.get("image") or {}).get("image")) or clean_str(site.get("fbImg"))

    offers = [o for n in jsonld_nodes(page_html) if "event" in schema_types(n) for o in n.get("offers") or []]
    tickets = [
        TicketTier(
            name=clean_str(o.get("name")) or "כניסה חופשית",
            price=to_decimal(o.get("price")) or Decimal(0),
            is_free=True,
            is_sold_out=o.get("availability") == "out_of_stock",
        )
        for o in offers
    ] or [TicketTier(name="כניסה חופשית בהרשמה מראש", price=Decimal(0), is_free=True)]

    events = []
    for line in _section(text, s.hours_heading):
        if not (m := _DAY.search(line)):
            continue
        day = date(today.year, int(m[2]), int(m[1]))
        day = day.replace(year=opening_year(day, today))
        if day < today:
            continue
        # Tiers for this day, or the site's all-days tiers.
        day_tickets = [
            t for t in tickets if (d := _TIER_DAY.search(t.name)) is None or d[1] == f"{day.day}.{day.month}"
        ]
        day_tickets = day_tickets or tickets
        start, end = (datetime.combine(day, datetime.strptime(t, "%H:%M").time(), tz) for t in (m[3], m[4]))
        events.append(
            ScrapedEvent(
                source=PoalimAtarimScraper.name,
                source_event_id=f"{eid}-{day:%Y%m%d}",
                url=clean_str(site.get("url")) or s.event_url_template.format(eid=eid),
                title=title,
                description=text or clean_str(site.get("description")),
                start_at=start,
                end_at=end,
                venue_name=venue,
                address=address,
                city=clean_str(town[1]) if town else None,
                latitude=float(waze[1]) if waze else None,
                longitude=float(waze[2]) if waze else None,
                event_types=["מוזיאונים ואתרים"],
                organizer=s.organizer,
                images=[image] if image else [],
                min_price=Decimal(0),
                max_price=Decimal(0),
                is_sold_out=all(t.is_sold_out for t in day_tickets),
                tickets=day_tickets,
                extra={"site_id": eid, "region_category": site.get("eventCategory")},
            )
        )
    return events
