"""FOMO (fomo-events.co) - Israeli self-service box office and resale marketplace: organisers (parties,
shows) open an event page and sell tickets on it.

The site's own JSON: ``/api/events?limit=&offset=`` is the whole public catalogue in one call (past
events too, test rows of new organisers among them; ``total`` / ``offset`` paging), each row the id,
title, slug, UTC start / end, the venue and city as typed, the site category (Music, Comedy, Festival,
Other...), the organiser and ``/api/media/event/<id>`` artwork. ``/api/events/<id>`` adds the HTML
description, the address, coordinates when set, the minimum age, the booking fee and the ticket types
(price in agorot, quantity, sold, sale window). Only current public events are read in detail:
1 + N requests a run (N = the upcoming events, 2 on 26.11.2026). Most events are secret-spot parties
whose venue is "ישראל" or a region ("צפון"): these placeholders give no venue and no city.

The home page's "אירועי ספורט" / "מופעים והופעות" sliders are Ticketmaster rows (``tm-<id>`` pages that
send to ticketmaster.com / ticketmaster.co.il), not FOMO's own sales: the Israeli ones are our
``ticketmaster`` source, the US games a sample of the Ticketmaster US catalogue. They are not read.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from decimal import Decimal
from typing import Any
from urllib.parse import quote

from pydantic import Field

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier, TierStatus
from partylinks.parsing import clean_str, parse_iso, to_decimal, to_float, to_int, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings


class FomoSettings(SourceSettings):
    site_url: str = "https://www.fomo-events.co"
    listing_path: str = "/api/events?limit={limit}&offset={offset}"
    detail_path: str = "/api/events/{id}"
    event_page_path: str = "/he/event/{slug}"
    og_image_path: str = "/api/events/{id}/og-image"
    page_size: int = 100
    # The booking fee added on the page when the event sets none (15000 agorot -> 157.50 shown).
    default_fee_percent: Decimal = Decimal(5)
    # Venue / city values that name no place (secret-spot parties): the country, a region.
    placeholder_places: list[str] = Field(
        default_factory=lambda: [
            "ישראל",
            "israel",
            "צפון",
            "דרום",
            "מרכז",
            "השרון",
            "בטבע",
            "בטבע היפה שלנו",
            "tba",
            "יפורסם",
        ]
    )


class FomoScraper(BaseScraper[FomoSettings]):
    name = "fomo"
    settings_model = FomoSettings

    def default_headers(self) -> dict[str, str]:
        return {"Accept": "application/json"}

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            url = s.site_url + s.listing_path.format(limit=s.page_size, offset=offset)
            page = await self.get_json(url, expect=dict)
            data = [r for r in page.get("data") or [] if isinstance(r, dict) and r.get("id")]
            rows.extend(data)
            offset += len(data)
            if not data or offset >= (to_int(page.get("total")) or 0):
                break
        upcoming = [r for r in rows if listed(r) and self._current(r)]
        upcoming.sort(key=lambda r: str(r.get("startsAt")))
        self.log.debug("catalogue: %d rows, %d upcoming", len(rows), len(upcoming))

        async def read(row: dict[str, Any]) -> list[ScrapedEvent]:
            detail = await self.get_json(s.site_url + s.detail_path.format(id=quote(str(row["id"]), safe="")))
            detail = detail.get("data", detail) if isinstance(detail, dict) else {}
            event = parse_event(merge(row, detail), s, self.timezone)
            if event is None or not is_current(event.start_at, event.end_at, RetentionSettings(), self.timezone):
                return []
            return [event]

        async for events in self.stream_isolated(upcoming, read, lambda r: f"event {r.get('id')}"):
            for event in events:
                yield event

    def _current(self, row: dict[str, Any]) -> bool:
        start = parse_iso(row.get("startsAt"), "UTC")
        end = parse_iso(row.get("endsAt"), "UTC")
        return start is not None and is_current(start, end, RetentionSettings(), self.timezone)


def listed(row: dict[str, Any]) -> bool:
    """A public, published event (private and draft pages are not listings)."""
    return not row.get("isPrivate") and str(row.get("status") or "PUBLISHED").upper() == "PUBLISHED"


def merge(row: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any]:
    """The detail answer over the listing row, but the listing's artwork path: the detail inlines the
    picture as a ``data:`` URI."""
    return {**row, **detail, "imageUrl": row.get("imageUrl")}


def place(value: Any, s: FomoSettings) -> str | None:
    """A venue / city as typed, or None for a placeholder (the country, a region, a blank)."""
    text = clean_str(value)
    if not text or text.lower() in s.placeholder_places:
        return None
    return text


def parse_event(row: dict[str, Any], s: FomoSettings, timezone: str) -> ScrapedEvent | None:
    """A listing row merged with its detail answer -> one event."""
    title = clean_str(row.get("title"))
    tz = clean_str(row.get("timezone")) or timezone
    start = parse_iso(row.get("startsAt"), "UTC")
    if not title or start is None or not listed(row):
        return None
    start = start.astimezone(zone(tz))
    end = parse_iso(row.get("endsAt"), "UTC")
    end = end.astimezone(zone(tz)) if end and end > start else None
    slug = clean_str(row.get("slug")) or str(row["id"])
    url = s.site_url + s.event_page_path.format(slug=quote(slug, safe=""))
    venue = place(row.get("venueName"), s)
    address = place(row.get("venueAddress"), s)
    city = place(row.get("city"), s)
    lat, lon = to_float(row.get("latitude")), to_float(row.get("longitude"))
    tiers = _tiers(row, s)
    prices = sorted(t.price for t in tiers if t.price is not None)
    organizer = row.get("organizer") if isinstance(row.get("organizer"), dict) else {}
    category = clean_str(row.get("category"))
    external = clean_str(row.get("externalProviderUrl")) if row.get("isExternal") else None
    return ScrapedEvent(
        source=FomoScraper.name,
        source_event_id=str(row["id"]),
        url=url,
        title=title,
        description=clean_str(row.get("description")),
        start_at=start,
        end_at=end,
        time_known=True,
        venue_name=venue or address,
        address=address if address != venue else None,
        city=city,
        latitude=lat if lat is not None and lon is not None else None,
        longitude=lon if lat is not None and lon is not None else None,
        source_genres=[category.lower()] if category and category.lower() != "other" else [],
        images=_images(row, s),
        tickets=tiers,
        min_price=prices[0] if prices else None,
        max_price=prices[-1] if prices else None,
        artists=[a for a in (_artist(x) for x in row.get("lineup") or []) if a],
        currency=clean_str(row.get("currency")) or "ILS",
        is_sold_out=bool(tiers) and all(t.status is TierStatus.SOLD_OUT for t in tiers),
        min_age=to_int(row.get("minAge")) or None,
        organizer=clean_str(organizer.get("name")),
        extra={"purchase_url": external or url},
    )


def _artist(item: Any) -> str | None:
    """A lineup entry: a name, or a dict with one."""
    return clean_str(item.get("name") if isinstance(item, dict) else item)


def _images(row: dict[str, Any], s: FomoSettings) -> list[str]:
    """The listing's artwork path; the page's og-image (the same artwork, cropped) when it has none."""
    path = clean_str(row.get("imageUrl"))
    if path and path.startswith("/"):
        return [s.site_url + path]
    if path and path.startswith("http"):
        return [path]
    return [s.site_url + s.og_image_path.format(id=quote(str(row["id"]), safe=""))]


def _tiers(row: dict[str, Any], s: FomoSettings) -> list[TicketTier]:
    fee_percent = to_decimal(row.get("handlingFeePercent"))
    if fee_percent is None:
        fee_percent = s.default_fee_percent
    if row.get("feeAbsorbedByOrganizer"):
        fee_percent = Decimal(0)
    now = datetime.now(zone("UTC"))
    tiers = []
    for t in row.get("ticketTypes") or []:
        if not isinstance(t, dict) or not clean_str(t.get("name")):
            continue
        agorot = to_decimal(t.get("price"))
        price = (agorot / 100).quantize(Decimal("0.01")) if agorot is not None else None
        fee = (price * fee_percent / 100).quantize(Decimal("0.01")) if price else None
        quantity, sold = to_int(t.get("quantity")), to_int(t.get("sold")) or 0
        remaining = max(quantity - sold, 0) if quantity is not None else None
        sale_start = parse_iso(t.get("salesStart"), "UTC")
        sale_end = parse_iso(t.get("salesEnd"), "UTC")
        if remaining == 0:
            status = TierStatus.SOLD_OUT
        elif sale_end and sale_end < now:
            status = TierStatus.CLOSED
        elif sale_start and sale_start > now:
            status = TierStatus.COMING_SOON
        else:
            status = TierStatus.ON_SALE
        tiers.append(
            TicketTier(
                source_ticket_id=clean_str(t.get("id")),
                name=str(clean_str(t.get("name"))),
                description=clean_str(t.get("description")),
                price=price,
                service_fee=fee or None,
                currency=clean_str(t.get("currency")) or clean_str(row.get("currency")) or "ILS",
                is_free=price == 0,
                status=status,
                remaining=remaining,
                sale_start=sale_start,
                sale_end=sale_end,
            )
        )
    return tiers
