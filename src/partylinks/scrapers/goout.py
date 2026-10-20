"""GO-OUT (go-out.co).

Listing: internal JSON API ``POST /endOne/getEventsByTypeNew`` paginated with
``skip``/``limit`` (this is what the site's "load more" scroll calls).
Details: the event page's ``__NEXT_DATA__`` holds the full event (its own
``Tickets`` array is only a placeholder). Every ticket tier - price rounds,
status, booking fee - comes from ``GET /endOne/loadEventTickets?eventUrl=<url>``,
the same call the event page makes. The page's schema.org offers are a fallback.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import Field

from partylinks.artists import ArtistProfile
from partylinks.models import ScrapedEvent, TicketTier, TierStatus
from partylinks.parsing import (
    ParseError,
    clean_str,
    html_to_text,
    next_data,
    parse_iso,
    to_decimal,
    to_float,
    to_int,
)
from partylinks.scrapers.base import BaseScraper, SourceSettings


class GoOutSettings(SourceSettings):
    base_url: str
    list_endpoint: str
    event_page_path: str
    tickets_endpoint: str
    image_url_templates: list[str] = Field(default_factory=list)
    event_types: list[str] = Field(min_length=1)
    location: str = "IL"
    page_size: int = Field(8, ge=1)
    max_pages: int = Field(100, ge=1)
    # Extra listing feeds behind site pages (GET ?limit&skip&recivedDate&location), e.g. /tickets/weekend.
    # They list events the by-type feed misses; only items of ``event_types`` are kept.
    extra_feeds: list[str] = Field(default_factory=list)
    extra_page_size: int = Field(50, ge=1)
    # The site's curated "artists" section (POST {skip}, 10 per page). Empty endpoint = skip.
    artists_endpoint: str = ""
    artist_image_template: str = ""  # {artist_url}, {timestamp}
    artist_page_path: str = "/artist/{artist_url}"


class GoOutScraper(BaseScraper[GoOutSettings]):
    name = "goout"
    settings_model = GoOutSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        refs = await self._list_event_urls()
        self.log.info("listed %d events", len(refs))
        if self.settings.artists_endpoint:
            self.artist_profiles = await self.optional("artists", self._list_artists()) or []
            self.log.info("listed %d artists", len(self.artist_profiles))
        async for event in self.stream_isolated(refs, self._fetch_event, label=lambda ref: ref):
            yield event

    async def _list_event_urls(self) -> list[str]:
        s = self.settings
        received = datetime.now(UTC).isoformat()

        async def page(index: int) -> tuple[list[dict[str, Any]], None]:
            payload = await self.post_json(
                s.base_url + s.list_endpoint,
                json={
                    "skip": index * s.page_size,
                    "limit": s.page_size,
                    "Types": s.event_types,
                    "location": s.location,
                    "recivedDate": received,
                },
                expect=dict,
            )
            return [e for e in payload.get("events") or [] if isinstance(e, dict)], None

        items = await self.paginate(page, s.page_size, s.max_pages)
        for feed in s.extra_feeds:
            extra = await self.optional(feed, self._list_feed(feed, received))
            items += [e for e in extra or [] if e.get("EventType") in s.event_types]
        return list(dict.fromkeys(str(item["Url"]) for item in items if item.get("Url")))

    async def _list_artists(self) -> list[ArtistProfile]:
        s = self.settings
        page_size = 10  # fixed by the site

        async def page(index: int) -> tuple[list[dict[str, Any]], None]:
            payload = await self.post_json(s.base_url + s.artists_endpoint, json={"skip": index * page_size}, expect=dict)
            return [a for a in payload.get("artists") or [] if isinstance(a, dict)], None

        return [
            ArtistProfile(
                name=name,
                source=self.name,
                kind=clean_str(a.get("type")),
                genre=clean_str(a.get("music_type")),
                image_url=s.artist_image_template.format(artist_url=slug, timestamp=a["profileImageTimeStamp"])
                if s.artist_image_template and a.get("profileImageTimeStamp") else None,
                spotify_url=clean_str(a.get("spotify_url")),
                profile_url=s.base_url + s.artist_page_path.format(artist_url=slug),
            )
            for a in await self.paginate(page, page_size, s.max_pages)
            if (name := clean_str(a.get("name"))) and (slug := clean_str(a.get("artistUrl")))
        ]

    async def _list_feed(self, feed: str, received: str) -> list[dict[str, Any]]:
        s = self.settings

        async def page(index: int) -> tuple[list[dict[str, Any]], None]:
            params = {"limit": s.extra_page_size, "skip": index * s.extra_page_size, "recivedDate": received,
                      "location": s.location}  # fmt: skip
            payload = await self.get_json(s.base_url + feed, params=params)
            events = payload.get("events") if isinstance(payload, dict) else None
            return [e for e in events or [] if isinstance(e, dict)], None

        return await self.paginate(page, s.extra_page_size, s.max_pages)

    async def _fetch_event(self, url_id: str) -> ScrapedEvent:
        s = self.settings
        page_url = s.base_url + s.event_page_path.format(url=url_id)
        page_html, tickets = await asyncio.gather(
            self.get_text(page_url),
            # Tiers endpoint down -> still save the event with schema.org offers.
            self.optional(f"{url_id} tickets", self.get_json(s.base_url + s.tickets_endpoint, params={"eventUrl": url_id})),
        )
        return parse_event(next_data(page_html), page_url, self.timezone, tickets, s.image_url_templates)


def parse_event(
    data: dict[str, Any],
    page_url: str,
    default_tz: str,
    tickets_payload: dict[str, Any] | None = None,
    image_url_templates: list[str] | None = None,
) -> ScrapedEvent:
    event = (data.get("props", {}).get("pageProps") or {}).get("event")
    if not event:
        raise ParseError("event missing from pageProps")

    tz = event.get("TimeZone") or default_tz
    currency = (event.get("EventCurrency") or {}).get("currency") or "ILS"
    location = event.get("Location") or {}
    schemas = [s for s in event.get("schemaOrg") or [] if isinstance(s, dict)]
    cover = event.get("CoverImageTimestamp")
    images = [t.format(event_id=event["_id"], cover_timestamp=cover) for t in image_url_templates or [] if cover]
    offers = [o for s in schemas for o in _as_list(s.get("offers")) if isinstance(o, dict)]

    tickets_payload = tickets_payload if isinstance(tickets_payload, dict) else {}
    raw_tickets = tickets_payload.get("tickets") if tickets_payload.get("status") else None
    raw_tickets = [t for t in raw_tickets or [] if isinstance(t, dict)]
    tickets = (
        [_parse_ticket(t, currency, tz) for t in raw_tickets]
        if raw_tickets
        else _parse_offers(offers, currency)
    )
    priced = [t.price for t in tickets if t.price is not None and t.is_active]

    return ScrapedEvent(
        source=GoOutScraper.name,
        source_event_id=str(event["_id"]),
        url=page_url,
        title=clean_str(event.get("Title")) or "",
        description=html_to_text(event.get("Description")),
        start_at=parse_iso(event.get("StartingDate"), tz),
        end_at=parse_iso(event.get("EndingDate"), tz),
        venue_name=_venue_from_address(event.get("Adress")),
        address=clean_str(event.get("EnglishAddress")) or clean_str(event.get("Adress")),
        city=clean_str(location.get("city")),
        latitude=to_float(location.get("lat")),
        longitude=to_float(location.get("lng")),
        source_genres=[g for g in event.get("MusicType") or [] if g],
        event_types=[t for t in [event.get("EventType")] if t],
        artists=[
            name
            for a in event.get("artists") or []
            if (name := clean_str(a.get("name") if isinstance(a, dict) else a))
        ],
        min_age=to_int(event.get("MinimumAge")),
        organizer=clean_str(event.get("ProducersName")),
        images=images,
        currency=currency,
        min_price=min(priced, default=None),
        max_price=max(priced, default=None),
        is_sold_out=bool(tickets) and all(t.is_sold_out for t in tickets),
        tickets=tickets,
        extra={
            "event_serial": event.get("EventSerial"),
            "publicity": event.get("EventPublicity"),
            "target_age_groups": event.get("targetAgeGroup") or [],
            "organizer_legal_name": event.get("organizer_legal_name"),
            "promoted": bool(event.get("goout_promoted")),
        },
    )


CENTS = Decimal("0.01")

# GO-OUT ticket `display` values. NOT_ACTIVE is rendered as "sold out" on the site.
_DISPLAY_STATUS = {
    "ACTIVE": TierStatus.ON_SALE,
    "SOLD_OUT": TierStatus.SOLD_OUT,
    "NOT_ACTIVE": TierStatus.SOLD_OUT,
    "COMING_SOON": TierStatus.COMING_SOON,
}


def _parse_ticket(ticket: dict[str, Any], currency: str, tz: str) -> TicketTier:
    price = to_decimal(ticket.get("Price"))
    commission = to_decimal(ticket.get("Commision"))
    rounds = sorted(
        (r for r in ticket.get("Rounds") or [] if isinstance(r, dict)), key=lambda r: to_int(r.get("RoundIndex")) or 0
    )
    status = _DISPLAY_STATUS.get(str(ticket.get("display")).upper())
    if status is None:
        status = TierStatus.ON_SALE if ticket.get("Active") else TierStatus.CLOSED
    return TicketTier(
        source_ticket_id=clean_str(ticket.get("ticket_id") or ticket.get("_id")),
        name=clean_str(ticket.get("Title")) or "Ticket",
        description=clean_str(ticket.get("Comment")),
        price=price,
        service_fee=(price * commission / 100).quantize(CENTS) if price and commission else None,
        price_rounds=[p for r in rounds if (p := to_float(r.get("Price"))) is not None],
        currency=currency,
        is_free=price == 0,
        status=status,
        sale_end=parse_iso(ticket.get("SaleEnd"), tz),
    )


def _parse_offers(offers: list[dict[str, Any]], currency: str) -> list[TicketTier]:
    """One tier per distinct schema.org Offer (the Event and Product blocks repeat them)."""
    tiers: dict[tuple[Any, ...], TicketTier] = {}
    for offer in offers:
        price = to_decimal(offer.get("price"))
        key = (offer.get("name"), price, offer.get("validFrom"), offer.get("priceValidUntil"))
        tiers.setdefault(
            key,
            TicketTier(
                name=clean_str(offer.get("name")) or "Lowest available price",
                price=price,
                currency=offer.get("priceCurrency") or currency,
                is_free=price == 0,
                is_sold_out=str(offer.get("availability", "")).endswith("SoldOut"),
                sale_start=parse_iso(offer.get("validFrom")),
                sale_end=parse_iso(offer.get("priceValidUntil")),
            ),
        )
    return list(tiers.values())


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _venue_from_address(address: str | None) -> str | None:
    """GO-OUT prefixes the street address with the venue name: "Venue, street, city"."""
    parts = [p.strip() for p in (address or "").split(",") if p.strip()]
    return parts[0] if len(parts) >= 4 else None
