"""ZYGO (zygo.co.il).

Listing: ``GET api.zygo.co.il/v1/event/<city>?limit&skip``. The API caps pages
at 10 items and returns them in a shuffled order, so ``skip`` alone can miss
events. The list is therefore walked repeatedly until a full pass yields no new
ids (bounded by ``max_passes``).
Details: the event page's ``__NEXT_DATA__`` holds the event incl. ticket tiers.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

from pydantic import Field

from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import ParseError, clean_str, html_to_text, next_data, parse_iso, to_decimal, to_float, to_int
from partylinks.scrapers.base import BaseScraper, SourceSettings


CENTS = Decimal("0.01")


class ZygoSettings(SourceSettings):
    site_url: str
    api_url: str
    city_slug: str
    list_path: str
    event_page_path: str
    page_size: int = Field(10, ge=1)
    max_pages: int = Field(50, ge=1)
    max_passes: int = Field(4, ge=1)


class ZygoScraper(BaseScraper[ZygoSettings]):
    name = "zygo"
    settings_model = ZygoSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        ids = await self._list_event_ids()
        self.log.info("listed %d events", len(ids))
        async for event in self.stream_isolated(ids, self._fetch_event, label=lambda i: i):
            yield event

    async def _list_event_ids(self) -> list[str]:
        s = self.settings
        url = s.api_url + s.list_path.format(city_slug=s.city_slug)

        async def page(index: int) -> tuple[list[dict[str, Any]], None]:
            batch = await self.get_json(url, params={"limit": s.page_size, "skip": index * s.page_size}, expect=list)
            return [item for item in batch if isinstance(item, dict)], None

        seen: dict[str, None] = {}
        for pass_no in range(1, s.max_passes + 1):
            before = len(seen)
            items = await self.paginate(page, s.page_size, s.max_pages)
            seen.update((str(item["id"]), None) for item in items if item.get("id"))
            self.log.debug("pass %d: %d new ids", pass_no, len(seen) - before)
            if len(seen) == before:
                break
        return list(seen)

    async def _fetch_event(self, event_id: str) -> ScrapedEvent:
        page_url = self.settings.site_url + self.settings.event_page_path.format(event_id=event_id)
        return parse_event(next_data(await self.get_text(page_url)), page_url)


def parse_event(data: dict[str, Any], page_url: str) -> ScrapedEvent:
    props = data.get("props", {}).get("pageProps") or {}
    event = props.get("event")
    if not event or not event.get("id"):
        raise ParseError("event missing from pageProps")

    location = event.get("location") or {}
    address = clean_str(location.get("address"))
    currency = event.get("currency") or "ILS"
    price_range = event.get("priceRange") or {}
    fee_rate = None if event.get("producerAbsorbsFee") else to_decimal(event.get("feeRate"))
    tickets = [_parse_ticket(t, currency, fee_rate) for t in event.get("tickets") or [] if isinstance(t, dict)]

    return ScrapedEvent(
        source=ZygoScraper.name,
        source_event_id=str(event["id"]),
        url=page_url,
        title=clean_str(event.get("title")) or "",
        description=html_to_text(event.get("description")) or clean_str(props.get("shortDescription")),
        start_at=parse_iso(event.get("startDate")),
        end_at=parse_iso(event.get("endDate")),
        address=address,
        city=_city_from_address(address),
        latitude=to_float(location.get("lat")),
        longitude=to_float(location.get("lng")),
        source_genres=[_label(t) for t in [*(event.get("tags") or []), *(event.get("eventTags") or [])] if _label(t)],
        artists=[_label(a) for a in event.get("artists") or [] if _label(a)],
        min_age=to_int(event.get("minAge")),
        organizer=clean_str((event.get("produced_by") or {}).get("name"))
        or clean_str((event.get("creator") or {}).get("username")),
        images=[
            img
            for img in [event.get("image"), event.get("whatsappImage"), *(event.get("images") or []), *(event.get("gallery") or [])]
            if isinstance(img, str) and img.startswith("http")
        ],
        currency=currency,
        min_price=to_decimal(price_range.get("min")),
        max_price=to_decimal(price_range.get("max")),
        is_sold_out=bool(tickets) and all(t.is_sold_out or not t.is_active for t in tickets),
        tickets=tickets,
        extra={
            "visibility": event.get("visibility"),
            "identifier": event.get("identifier"),
            "fee_percent": event.get("feeRate"),
            "producer_absorbs_fee": event.get("producerAbsorbsFee"),
            "hot_message": (event.get("initalMessage") or {}).get("message"),
        },
    )


def _parse_ticket(ticket: dict[str, Any], currency: str, fee_rate: Decimal | None) -> TicketTier:
    price = to_decimal(ticket.get("price"))
    return TicketTier(
        source_ticket_id=clean_str(ticket.get("id") or ticket.get("_id")),
        name=clean_str(ticket.get("title")) or "Ticket",
        description=clean_str(ticket.get("description")),
        price=price,
        original_price=to_decimal(ticket.get("fullPrice")),
        service_fee=(price * fee_rate / 100).quantize(CENTS) if price and fee_rate else None,
        currency=ticket.get("currency") or currency,
        is_free=bool(ticket.get("freeTicket")) or price == 0,
        is_active=bool(ticket.get("active", True)),
        is_sold_out=ticket.get("isHaveTickets") is False,
        sale_start=parse_iso(ticket.get("startSaleDate")),
        sale_end=parse_iso(ticket.get("endSaleDate")),
    )


def _city_from_address(address: str | None) -> str | None:
    """Google-formatted addresses end with "..., <city>, <country>"."""
    parts = [p.strip() for p in (address or "").split(",") if p.strip()]
    return parts[-2] if len(parts) >= 2 else None


def _label(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("name") or value.get("title")
    return clean_str(value)
