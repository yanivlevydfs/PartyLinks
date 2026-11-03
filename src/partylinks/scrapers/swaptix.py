"""SwapTix (swaptix.co.il) - Israeli ticket resale marketplace: people selling tickets they bought for
concerts, shows, stand-up and festivals.

The site's own JSON (robots.txt allows ``/api/events/``): ``/api/events/external`` is the catalogue
(~160 upcoming events, mostly gathered from Muzi and Tickchak: Hebrew name, venue, city, artwork,
description); ``/api/events/<id>/listings`` the tickets on sale for one event (date and UTC start, section,
row, seats, quantity, price per ticket). Only catalogue events with tickets on sale are events here,
one per date: a catalogue entry with no seller duplicates its primary source. 1 + ~160 requests a run.
The page is ``/events/<id>``. Resale: ``extra.resale`` as for viagogo.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any
from urllib.parse import quote

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import clean_str, parse_iso, to_decimal, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings


class SwapTixSettings(SourceSettings):
    site_url: str = "https://swaptix.co.il"
    events_path: str = "/api/events/external"
    listings_path: str = "/api/events/{id}/listings"
    event_page_path: str = "/events/{id}"


class SwapTixScraper(BaseScraper[SwapTixSettings]):
    name = "swaptix"
    settings_model = SwapTixSettings

    def default_headers(self) -> dict[str, str]:
        return {"Accept": "application/json"}

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        catalogue = await self.get_json(s.site_url + s.events_path, expect=list)
        catalogue = [e for e in catalogue if isinstance(e, dict) and e.get("id")]
        self.log.debug("catalogue: %d events", len(catalogue))

        async def read(item: dict[str, Any]) -> list[ScrapedEvent]:
            path = s.listings_path.format(id=quote(str(item["id"]), safe=""))
            payload = await self.get_json(s.site_url + path)
            listings = payload if isinstance(payload, list) else (payload or {}).get("listings") or []
            events = resale_events(item, listings, s, self.timezone)
            return [e for e in events if is_current(e.start_at, e.end_at, RetentionSettings(), self.timezone)]

        async for events in self.stream_isolated(catalogue, read, lambda e: f"event {e.get('id')}"):
            for event in events:
                yield event


def resale_events(item: dict[str, Any], listings: list[Any], s: SwapTixSettings, timezone: str) -> list[ScrapedEvent]:
    """A catalogue event's listings on sale -> one event per start, its tickets the listings."""
    by_start: dict[str, list[dict[str, Any]]] = {}
    for listing in listings:
        if isinstance(listing, dict) and (listing.get("quantityAvailable") or 0) > 0:
            when = listing.get("eventStartsAt") or listing.get("eventDateISO")
            if when:
                by_start.setdefault(str(when), []).append(listing)
    title = clean_str(item.get("nameHe") or item.get("titleHe")) or clean_str(item.get("name"))
    if not title:
        return []
    url = s.site_url + s.event_page_path.format(id=quote(str(item["id"]), safe=""))
    image = clean_str(item.get("imageUrl") or item.get("image"))
    events = []
    for when, rows in by_start.items():
        start = parse_iso(when, "UTC")
        if start is None:
            continue
        start = start.astimezone(zone(timezone))
        tiers = [
            TicketTier(
                source_ticket_id=clean_str(row.get("id")),
                name=" ".join(p for p in (clean_str(row.get("section")), _row(row.get("row"))) if p) or "כרטיס",
                price=price,
                description=f"{row.get('quantityAvailable')} כרטיסים"
                if (row.get("quantityAvailable") or 0) > 1
                else None,
            )
            for row in rows
            if (price := to_decimal(row.get("priceCredits"))) is not None
        ]
        prices: list[Decimal] = sorted(t.price for t in tiers if t.price is not None)
        events.append(
            ScrapedEvent(
                source=SwapTixScraper.name,
                source_event_id=f"{item['id']}:{start.isoformat()}",
                url=url,
                title=title,
                description=clean_str(item.get("descriptionHe") or item.get("description")),
                start_at=start,
                time_known="T" in when,
                venue_name=clean_str(item.get("venueHe") or item.get("venue")),
                city=clean_str(item.get("cityHe") or item.get("city")),
                source_genres=[c for c in (clean_str(item.get("category")),) if c],
                images=[image] if image and image.startswith("http") else [],
                tickets=tiers,
                min_price=prices[0] if prices else None,
                max_price=prices[-1] if prices else None,
                extra={"purchase_url": url, "resale": True},
            )
        )
    return events


def _row(value: Any) -> str | None:
    row = clean_str(value)
    return f"שורה {row}" if row else None
