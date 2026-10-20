"""Ticketmaster Israel (ticketmaster.co.il) - concerts, shows and festivals nationwide.

Listing: the event sitemap ``GET /wbtxapi/api/v1/siteMap/event`` links every on-sale event
(``/event/<code>/ALL/iw`` and ``/en``); the event code is taken from the links.
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


class TicketmasterScraper(BaseScraper[TicketmasterSettings]):
    name = "ticketmaster"
    settings_model = TicketmasterSettings

    def default_headers(self) -> dict[str, str]:
        return {"Referer": self.settings.site_url + "/"}

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        codes = parse_sitemap(await self.get_text(s.site_url + s.sitemap_path))
        self.log.info("listed %d events", len(codes))
        async for events in self.stream_isolated(codes, self._fetch_event, label=str):
            for event in events:
                yield event

    async def _api(self, path: str) -> Any:
        return unwrap(await self.get_json(self.settings.site_url + path, expect=dict))

    async def _fetch_event(self, code: str) -> list[ScrapedEvent]:
        s = self.settings
        detail, performances = await asyncio.gather(
            self._api(s.event_path.format(code=code)), self._api(s.performances_path.format(code=code))
        )
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
        return events


def unwrap(payload: dict[str, Any]) -> Any:
    if payload.get("status") != "SUCCESS":
        raise ParseError(f"API status {payload.get('status')}: {payload.get('errors')}")
    return payload.get("data")


def parse_sitemap(xml: str) -> list[str]:
    """Event codes in sitemap order (each event is listed once per language)."""
    return list(dict.fromkeys(_EVENT_LINK.findall(xml)))


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
    return TierStatus.ON_SALE if "onsale" in value else TierStatus.CLOSED


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
