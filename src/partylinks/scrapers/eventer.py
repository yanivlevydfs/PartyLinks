"""Eventer (eventer.co.il).

Listing: ``/sliders/categories/<category>/events.js`` returns the complete category
slider as JSON in one response (the site has no further pages for it).
Details: ``/events/explainNames/<linkName>.js`` (event + JSON-LD) and
``/events/<id>/ticketTypes.js`` (all ticket tiers incl. free registrations).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from pydantic import Field

from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import ParseError, clean_str, html_to_text, parse_iso, to_decimal, to_float, to_int
from partylinks.scrapers.base import BaseScraper, SourceBlockedError, SourceSettings

UNLIMITED = -1


class EventerSettings(SourceSettings):
    base_url: str
    categories: list[str] = Field(min_length=1)
    list_path: str
    event_details_path: str
    ticket_types_path: str


class EventerScraper(BaseScraper[EventerSettings]):
    name = "eventer"
    settings_model = EventerSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        link_names = await self._list_link_names()
        self.log.info("listed %d events", len(link_names))
        async for event in self.stream_isolated(link_names, self._fetch_event, label=lambda n: n):
            yield event

    async def _list_link_names(self) -> list[str]:
        s = self.settings
        seen: dict[str, None] = {}
        for category in s.categories:
            try:  # one renamed/broken category must not hide the others
                payload = await self.get_json(s.base_url + s.list_path.format(category=category), expect=dict)
            except SourceBlockedError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.record_failure(f"category {category}", exc)
                continue
            slides = [slide for slide in payload.get("slides") or [] if isinstance(slide, dict)]
            seen.update((str(slide["linkName"]), None) for slide in slides if slide.get("linkName"))
        if not seen and self.failures:
            raise ParseError("every category listing failed")
        return list(seen)

    async def _fetch_event(self, link_name: str) -> ScrapedEvent:
        s = self.settings
        details = await self.get_json(s.base_url + s.event_details_path.format(link_name=link_name), expect=dict)
        event = details.get("event")
        event_id = event.get("_id") if isinstance(event, dict) else None
        if not event_id:
            raise ParseError("event id missing")
        # Ticket endpoint down -> still save the event, without tiers.
        tickets_payload = await self.optional(
            f"{link_name} tickets", self.get_json(s.base_url + s.ticket_types_path.format(event_id=event_id), expect=dict)
        ) or {}
        return parse_event(details, tickets_payload, f"{s.base_url}/{link_name}", self.timezone)


def parse_event(details: dict[str, Any], tickets_payload: dict[str, Any], page_url: str, default_tz: str) -> ScrapedEvent:
    event = details.get("event") or {}
    if not event.get("_id"):
        raise ParseError("event missing")

    ld = details.get("jsonLdData") or {}
    location = event.get("location") or {}
    schedule = event.get("schedule") or {}
    tz = location.get("timezone") or default_tz
    currency = event.get("defaultCurrency") or "ILS"
    categories = event.get("eventCategories") or {}
    platform = event.get("ticketPlatform") or {}
    location_description = clean_str(event.get("locationDescription"))

    tickets = [_parse_ticket(t, currency) for t in tickets_payload.get("ticketTypes") or [] if isinstance(t, dict)]
    priced = [t.price for t in tickets if t.price is not None and t.is_active]

    return ScrapedEvent(
        source=EventerScraper.name,
        source_event_id=str(event["_id"]),
        url=page_url,
        title=clean_str(event.get("name")) or clean_str(platform.get("name")) or "",
        description=html_to_text(event.get("eventDesc")),
        start_at=parse_iso(ld.get("startDate")) or parse_iso(schedule.get("start"), tz),
        end_at=parse_iso(ld.get("endDate")) or parse_iso(schedule.get("end"), tz),
        venue_name=location_description.split(",")[0].strip() if location_description else None,
        address=location_description,
        city=clean_str(location.get("addressLocality")),
        latitude=to_float(location.get("latitude")),
        longitude=to_float(location.get("longitude")),
        event_types=[
            c for c in [categories.get("topEventCategoryString"), *(categories.get("subEventCategoryStrings") or [])] if c
        ],
        min_age=to_int(event.get("ageLimit")),
        organizer=clean_str((event.get("producer") or {}).get("publicName")),
        images=_images(event),
        currency=currency,
        min_price=min(priced, default=None),
        max_price=max(priced, default=None),
        is_sold_out=tickets_payload.get("totalRemaining") == 0,
        tickets=tickets,
        extra={
            "link_name": event.get("linkName"),
            "status": event.get("status"),
            "open_doors": schedule.get("openDoors"),
            "allow_multiple_tickets": tickets_payload.get("allowMultipleTickets"),
        },
    )


def _parse_ticket(ticket: dict[str, Any], currency: str) -> TicketTier:
    price = to_decimal(ticket.get("price"))
    remaining = to_int(ticket.get("remaining"))
    remaining = None if remaining in (None, UNLIMITED) else remaining
    return TicketTier(
        source_ticket_id=clean_str(ticket.get("_id")),
        name=clean_str(ticket.get("name")) or "Ticket",
        description=clean_str(ticket.get("description")),
        price=price,
        original_price=to_decimal(ticket.get("originalPrice")),
        currency=currency,
        is_free=price == 0,
        is_active=not ticket.get("isPurchaseWithCouponOnly", False),
        is_sold_out=remaining == 0,
        remaining=remaining,
        sale_end=parse_iso(ticket.get("toDate")),
    )


def _images(event: dict[str, Any]) -> list[str]:
    """Marketing artwork first (default/wide/square/mobile), then page photos."""
    platform_images = (event.get("ticketPlatform") or {}).get("images") or {}
    candidates = [
        *(platform_images.get(k) for k in ("imageDefault", "imageWide", "imageSquare", "imageMobile")),
        *(event.get(k) for k in ("fbPhoto", "mobilePhoto", "thumbnail", "background", "mobileBackground")),
    ]
    return [c for c in candidates if isinstance(c, str) and c.startswith("http")]
