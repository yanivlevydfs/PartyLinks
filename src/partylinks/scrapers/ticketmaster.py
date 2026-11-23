"""Ticketmaster Israel (ticketmaster.co.il) - concerts, shows and festivals nationwide.

Listing: the event sitemap ``GET /wbtxapi/api/v1/siteMap/event`` links most on-sale events
(``/event/<code>/ALL/iw`` and ``/en``); the event code is taken from the links. The sitemap
misses some (Nov 2026: Gordon Fight Nights, Solstars, two Eilat 4PASS passes), so the homepage
picks (``getAllTopEvent``), each category's spotlight (``getSpotLightData``) and every venue page
seen (``getAllEventsDetailByVenueCode``: the venue's dates of every event) add theirs, venue
pages repeated until no new event appears. The site's search API is closed (POST answers a 301
to a 404 page) and event-group pages list no events, so neither is read.
Details per event, fetched in parallel (all ``bxcached`` JSON, enveloped as
``{status: "SUCCESS", data}``):
* ``getEventDetail/<code>/INTERNET/iw`` - name, HTML description, image file names, venue,
  city and coordinates, category, promoter and the "from" price (whole shekels);
* ``getPerformanceList/<code>/INTERNET/iw`` - one row per date: ``performanceCode``,
  ``performanceDate`` (epoch ms) and ``status`` (``s01_onsale``, ``s02_soldout``,
  ``s00_closed``);
* ``getPriceByProfiles/<code>/<perf>/INTERNET/iw`` - per date, price levels per sales
  profile (full price plus card/club discount profiles), amounts in agorot. Every profile
  repeats the same face value in ``value``; discounts only lower ``minPrice``.
One ScrapedEvent per upcoming performance; the public purchase page is
``/performance/<code>/<perf>/ALL/iw``. The site exposes no street address, only venue,
city and coordinates.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from pydantic import Field

from partylinks.models import ScrapedEvent, TicketTier, TierStatus
from partylinks.parsing import (
    ParseError,
    clean_str,
    from_unix,
    html_to_text,
    to_decimal,
    to_float,
)
from partylinks.scrapers.base import BaseScraper, SourceSettings

AGOROT = Decimal(100)
_EVENT_LINK = re.compile(r"/event/([A-Za-z0-9_-]+)/")
_TEST_TITLE = re.compile(r"^test\b", re.IGNORECASE)  # the box office's own test listings ("Test MMISM 2")
_ACTS = re.compile(r"\s+(?:\+|&|VS|x)\s+", re.IGNORECASE)


class TicketmasterSettings(SourceSettings):
    site_url: str
    sitemap_path: str
    event_path: str
    performances_path: str
    prices_path: str
    performance_page_path: str
    image_url_template: str
    wide_image_url_template: str
    top_events_path: str = ""  # homepage picks; empty = not read
    spotlight_path: str = ""  # per category: {category}
    spotlight_categories: list[str] = Field(default_factory=list)
    venue_path: str = ""  # a venue's performances: {venue}


class TicketmasterScraper(BaseScraper[TicketmasterSettings]):
    name = "ticketmaster"
    settings_model = TicketmasterSettings

    def default_headers(self) -> dict[str, str]:
        return {"Referer": self.settings.site_url + "/"}

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        codes = parse_sitemap(await self.get_text(s.site_url + s.sitemap_path))
        if not codes:
            raise ParseError("no /event/<code>/ link in the sitemap")
        venues: set[str] = set()
        if s.top_events_path:
            top = await self.optional("top events", self._api(s.top_events_path))
            codes += listed_codes(top, venues)
        for category in s.spotlight_categories:
            spot = await self.optional(f"{category} spotlight", self._api(s.spotlight_path.format(category=category)))
            codes += listed_codes(spot, venues)
        seen: set[str] = set()
        read_venues: set[str] = set()
        batch = list(dict.fromkeys(codes))
        while batch:
            seen.update(batch)
            self.log.debug("listed %d events", len(batch))
            async for venue, events in self.stream_isolated(batch, self._fetch_event, label=str):
                venues.update([venue] if venue else [])
                for event in events:
                    yield event
            more: list[str] = []
            for venue in sorted(venues - read_venues) if s.venue_path else []:
                read_venues.add(venue)
                page = await self.optional(f"venue {venue}", self._api(s.venue_path.format(venue=venue)))
                more += venue_codes(page)
            batch = [c for c in dict.fromkeys(more) if c not in seen]

    async def _api(self, path: str) -> Any:
        return unwrap(await self.get_json(self.settings.site_url + path, expect=dict))

    async def _fetch_event(self, code: str) -> tuple[str | None, list[ScrapedEvent]]:
        """The event's venue code (its venue page may list more events) and its upcoming dates."""
        s = self.settings
        detail, performances = await asyncio.gather(
            self._api(s.event_path.format(code=code)), self._api(s.performances_path.format(code=code))
        )
        venue = clean_str(detail.get("venueId")) if isinstance(detail, dict) else None
        if _TEST_TITLE.match(clean_str((detail or {}).get("eventName")) or ""):
            return venue, []
        events = []
        for perf in upcoming(performances, datetime.now(UTC)):
            event = parse_event(detail, perf, None, s, self.timezone)
            if self.wanted(event):  # tier prices cost one request per date: only for kept events
                prices = await self.optional(
                    f"{event.source_event_id} prices",
                    self._api(s.prices_path.format(code=code, perf=perf.get("performanceCode"))),
                )
                if prices:
                    event = parse_event(detail, perf, prices, s, self.timezone)
            events.append(event)
        return venue, events


def unwrap(payload: dict[str, Any]) -> Any:
    if payload.get("status") != "SUCCESS":
        raise ParseError(f"API status {payload.get('status')}: {payload.get('errors')}")
    return payload.get("data")


def parse_sitemap(xml: str) -> list[str]:
    """Event codes in sitemap order (each event is listed once per language)."""
    return list(dict.fromkeys(_EVENT_LINK.findall(xml)))


def listed_codes(data: Any, venues: set[str]) -> list[str]:
    """Event codes of the homepage picks / a spotlight (groups have none); their venues go to ``venues``."""
    items = data.get("elements") if isinstance(data, dict) else data
    codes = []
    for item in items if isinstance(items, list) else []:
        event = item.get("event") if isinstance(item, dict) and "event" in item else item
        if not isinstance(event, dict):
            continue
        if code := clean_str(event.get("btxEventId") or event.get("eventCode")):
            codes.append(code)
        if venue := clean_str(event.get("venueCode") or event.get("venueId")):
            venues.add(venue)
    return codes


def venue_codes(data: Any) -> list[str]:
    """Event codes of a venue page's performances, in order."""
    items = data.get("performanceItems") if isinstance(data, dict) else None
    codes = (clean_str(p.get("eventCode")) for p in items or [] if isinstance(p, dict))
    return list(dict.fromkeys(c for c in codes if c))


def upcoming(performances: Any, now: datetime) -> list[dict[str, Any]]:
    return [
        p
        for p in performances or []
        if isinstance(p, dict) and (start := from_unix(p.get("performanceDate"))) and start >= now
    ]


def parse_event(
    detail: dict[str, Any], perf: dict[str, Any], prices: Any, s: TicketmasterSettings, timezone: str
) -> ScrapedEvent:
    code, perf_code = clean_str(detail.get("eventCode")), clean_str(perf.get("performanceCode"))
    if not code or not perf_code:
        raise ParseError("eventCode/performanceCode missing")
    start = from_unix(perf.get("performanceDate"))
    status = _status(perf.get("status"))
    tickets = _parse_tiers(prices, status)
    if not tickets and (price := to_decimal(detail.get("price"))) is not None:
        # No price list fetched: the event's "from" price stands in as one tier.
        tickets = [TicketTier(name="כרטיס", price=price, is_free=price == 0, status=status)]
    priced = [t.price for t in tickets if t.price is not None and t.is_active]
    title = clean_str(detail.get("eventName")) or ""
    category = clean_str(detail.get("eventCategory"))
    # File names may hold spaces / Hebrew ("מופע פתיחה 610 על 336.jpg"): percent-encode them.
    image, wide = clean_str(detail.get("image")), clean_str(detail.get("wideImage"))
    lat, lon = to_float(detail.get("venueLatitude")), to_float(detail.get("venueLongitude"))
    return ScrapedEvent(
        source=TicketmasterScraper.name,
        source_event_id=f"{code}-{perf_code}",
        url=s.site_url + s.performance_page_path.format(code=code, perf=perf_code),
        title=title,
        description=html_to_text(detail.get("eventDescription")),
        start_at=start.astimezone(ZoneInfo(timezone)) if start else None,
        venue_name=clean_str(perf.get("venueName") or detail.get("venueName")),
        city=clean_str(perf.get("venueCity") or detail.get("venueCity")),
        latitude=lat or None,
        longitude=lon or None,
        source_genres=[g for g in (clean_str(detail.get("subCategory")),) if g],
        event_types=[t for t in (category,) if t],
        # ponytail: concert titles are the act ("Boy George", "פס חמישי + CIAM"); other
        # categories (stage, family) are not artists. Upgrade: match the artist book.
        artists=[a for a in _ACTS.split(title) if a] if category == "MUSIC" else [],
        organizer=clean_str(detail.get("promoterName")),
        images=[
            *([s.image_url_template.format(image=quote(image))] if image else []),
            *([s.wide_image_url_template.format(image=quote(wide))] if wide else []),
        ],
        min_price=min(priced, default=None),
        max_price=max(priced, default=None),
        is_sold_out=status is TierStatus.SOLD_OUT,
        tickets=tickets,
        extra={
            "event_code": code,
            "performance_code": perf_code,
            "status": clean_str(perf.get("status")),
            "venue_id": clean_str(detail.get("venueId")),
            "seating": clean_str(detail.get("seatPlanSelectionValue")),
            "rules": html_to_text(detail.get("eventRules")),
        },
    )


def _status(raw: Any) -> TierStatus:
    value = str(raw or "")
    if "soldout" in value:
        return TierStatus.SOLD_OUT
    # s18_low_availability still sells (last seats); s00_closed / s13_old_over do not.
    return TierStatus.ON_SALE if "onsale" in value or "low_availability" in value else TierStatus.CLOSED


def _parse_tiers(prices: Any, status: TierStatus) -> list[TicketTier]:
    """Public face-value tiers: one per price level, discount profiles collapse onto it."""
    if not isinstance(prices, dict):
        return []
    levels: dict[str, TicketTier] = {}
    for profile in prices.values():
        for level in profile if isinstance(profile, list) else []:
            code = clean_str(level.get("code")) if isinstance(level, dict) else None
            value = to_decimal(level.get("value")) if code else None
            if code is None or value is None or code in levels:
                continue
            price, fee = value / AGOROT, to_decimal(level.get("servicePrice"))
            levels[code] = TicketTier(
                source_ticket_id=code,
                name=clean_str(level.get("description")) or "כרטיס",
                price=price,
                service_fee=fee / AGOROT if fee else None,
                is_free=price == 0,
                status=status,
            )
    return list(levels.values())
