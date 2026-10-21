"""Selector (selector.org.il).

Listing: ``GET api.selector.org.il/event/list?skip&take&dateType=start`` returns
``{list, totalCount}``; pages are walked until ``totalCount`` is reached
(the API rejects ``take`` above 20).
Details: ``GET /event?id=<id>`` adds the public link, description, age policy
and the full ticket list.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import Field

from partylinks.models import ScrapedEvent, TicketTier, TierStatus
from partylinks.parsing import ParseError, clean_str, html_to_text, parse_iso, to_decimal, to_int
from partylinks.scrapers.base import BaseScraper, SourceSettings

CENTS = Decimal("0.01")


class SelectorSettings(SourceSettings):
    site_url: str
    api_url: str
    list_path: str
    event_path: str
    event_page_path: str
    page_size: int = Field(20, ge=1, le=20)
    max_pages: int = Field(50, ge=1)
    allowed_event_types: list[str] = Field(default_factory=list)


class SelectorScraper(BaseScraper[SelectorSettings]):
    name = "selector"
    settings_model = SelectorSettings

    def default_headers(self) -> dict[str, str]:
        return {"Origin": self.settings.site_url, "Referer": self.settings.site_url + "/"}

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        ids = await self._list_event_ids()
        self.log.info("listed %d events", len(ids))
        async for event in self.stream_isolated(ids, self._fetch_event, label=str):
            yield event

    async def _list_event_ids(self) -> list[int]:
        s = self.settings
        allowed = set(s.allowed_event_types)

        async def page(index: int) -> tuple[list[dict[str, Any]], int | None]:
            payload = await self.get_json(
                s.api_url + s.list_path,
                params={"skip": index * s.page_size, "take": s.page_size, "dateType": "start"},
                expect=dict,
            )
            return [i for i in payload.get("list") or [] if isinstance(i, dict)], payload.get("totalCount")

        items = await self.paginate(page, s.page_size, s.max_pages)
        return list(
            dict.fromkeys(
                item["id"]
                for item in items
                if item.get("id") is not None and (not allowed or allowed & set(item.get("type") or []))
            )
        )

    async def _fetch_event(self, event_id: int) -> ScrapedEvent:
        s = self.settings
        event = await self.get_json(s.api_url + s.event_path, params={"id": event_id}, expect=dict)
        return parse_event(event, s.site_url + s.event_page_path)


def parse_event(event: dict[str, Any], page_url_template: str) -> ScrapedEvent:
    if not event.get("id") or not event.get("eventLink"):
        raise ParseError("event id/link missing")

    settings = event.get("eventSettings") or {}
    fee_rate = None if settings.get("includeServiceFee") else to_decimal(event.get("feePercent"))
    tickets = [
        _parse_ticket(t, fee_rate)
        for t in event.get("tickets") or []
        if isinstance(t, dict) and not t.get("isDeleted") and not t.get("isOnlyByLinkAvailable")
    ]
    priced = [t.price for t in tickets if t.price is not None and t.is_active]
    pictures = [m["link"] for m in event.get("mediaFiles") or [] if m.get("type") == "picture" and m.get("link")]
    pictures += [b for b in (event.get("backgroundImage"), event.get("backgroundImageSmall")) if b]
    profile = event.get("organizerProfile") or {}

    return ScrapedEvent(
        source=SelectorScraper.name,
        source_event_id=str(event["id"]),
        url=page_url_template.format(event_link=event["eventLink"]),
        title=clean_str(event.get("name")) or "",
        description=html_to_text(event.get("description")),
        start_at=parse_iso(event.get("startDate")),
        end_at=parse_iso(event.get("endDate")),
        address=clean_str(event.get("address")),
        event_types=[t for t in event.get("type") or [] if t],
        min_age=to_int(event.get("minAge")),
        organizer=clean_str(profile.get("name")),
        images=pictures,
        min_price=min(priced, default=None),
        max_price=max(priced, default=None),
        is_sold_out=event.get("ticketsInStock") is False,
        tickets=tickets,
        extra={
            "event_link": event.get("eventLink"),
            "audience_sex": event.get("sex"),
            "requires_approval": event.get("isNeedConfirmation"),
            "fee_percent": event.get("feePercent"),
            "organizer_instagram": clean_str(profile.get("instagram")),
            "organizer_facebook": clean_str(profile.get("facebook")),
        },
    )


def _parse_ticket(ticket: dict[str, Any], fee_rate: Decimal | None) -> TicketTier:
    price = to_decimal(ticket.get("currentPrice"))
    return TicketTier(
        source_ticket_id=str(ticket["id"]) if ticket.get("id") is not None else None,
        name=clean_str(ticket.get("name")) or "Ticket",
        description=clean_str(ticket.get("note")),
        price=price,
        original_price=to_decimal(ticket.get("regularPrice")),
        # Selector reports the fee as a fraction (0.059 = 5.9%).
        service_fee=(price * fee_rate).quantize(CENTS) if price and fee_rate else None,
        admits=to_int(ticket.get("composition")) or 1,
        is_free=price == 0,
        status=_status(ticket),
        sale_start=parse_iso(ticket.get("startSellingDate")),
        sale_end=parse_iso(ticket.get("endSellingDate")),
    )


def _status(ticket: dict[str, Any]) -> TierStatus:
    if ticket.get("isSoldOut"):
        return TierStatus.SOLD_OUT
    if not ticket.get("isActive", True) or not ticket.get("canBeBooked", True):
        return TierStatus.CLOSED
    starts = parse_iso(ticket.get("startSellingDate")) if ticket.get("isTimePlanned") else None
    if starts and starts > datetime.now(UTC):
        return TierStatus.COMING_SOON
    return TierStatus.ON_SALE
