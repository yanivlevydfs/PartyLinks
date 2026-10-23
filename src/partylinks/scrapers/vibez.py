"""Vibez (vibez.io) - ticketing for communities and party producers: raves, ecstatic dance, club nights.

A Next.js app on a public Rails JSON API (``api.vibez.io/api/v102/buyer``); robots.txt is absent.
Listing: ``/events?page=N`` gives 20 upcoming events a page (5 pages, ~90): name, place, start/end,
cover and producer. Details: ``/events/{id}`` adds the description (HTML), and
``/events/{id}/products`` the ticket tiers with price, stock and sale window. The site has no
categories (``tags`` is always empty), so genres come from the name and description.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
from pydantic import Field

from partylinks.models import ScrapedEvent, TicketTier, TierStatus
from partylinks.parsing import ParseError, clean_str, html_to_text, parse_iso, to_decimal
from partylinks.scrapers.base import BaseScraper, SourceSettings

# Products that are entry; accommodation, food and "other" add-ons are not.
_ENTRY = {"entry_ticket", "child_ticket"}
_SOLD_OUT = re.compile(r"sold\s*out|אזל", re.IGNORECASE)
# A place named only for the country, or directions to a secret one ("50 דק צפונית מתל אביב",
# "55 min from tel aviv"), is no place: its city would be the one it is measured from.
_NO_PLACE = {"israel", "ישראל", "northern israel", "nature", "somewhere in nature", "דיגיטלי", "מרכז"}
_DIRECTIONS = re.compile(
    r"\b(?:from|min|minutes|hours?)\b|דק[׳']?(?!\w)|דקות|שעה|שעתיים|צפונית|דרומית|מזרחית|מערבית", re.IGNORECASE
)
_STREET = re.compile(r"\d|,")
MAX_SPAN = timedelta(days=7)


class VibezSettings(SourceSettings):
    site_url: str
    api_url: str
    max_pages: int = Field(20, ge=1)


class VibezScraper(BaseScraper[VibezSettings]):
    name = "vibez"
    settings_model = VibezSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        listed: dict[str, dict[str, Any]] = {}
        for page in range(1, s.max_pages + 1):
            try:
                items = (await self.get_json(f"{s.api_url}/events", params={"page": page})).get("events") or []
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 400:  # "expected :page in 1..5": past the last page
                    break
                raise
            if not items:
                break
            listed.update((i["id"], i) for i in items if isinstance(i, dict) and i.get("id"))
        self.log.info("listed %d events", len(listed))
        async for event in self.stream_isolated(list(listed), self._fetch_event, label=lambda i: i):
            yield event

    async def _fetch_event(self, event_id: str) -> ScrapedEvent | None:
        s = self.settings
        detail = (await self.get_json(f"{s.api_url}/events/{event_id}")).get("event") or {}
        products = (await self.get_json(f"{s.api_url}/events/{event_id}/products")).get("products") or []
        return parse_event(detail, products, s.site_url, self.timezone)


def parse_event(
    item: dict[str, Any], products: list[dict[str, Any]], site_url: str, timezone: str
) -> ScrapedEvent | None:
    """One event with its entry tiers; None when it is not on sale (cancelled, draft)."""
    if item.get("status") not in (None, "active"):
        return None
    event_id, title = clean_str(item.get("id")), clean_str(item.get("name"))
    start = parse_iso(item.get("start_time"), item.get("timezone") or timezone)
    if not event_id or not title or not start:
        raise ParseError(f"{event_id}: event without id, name or start")
    place = clean_str(item.get("location"))
    place = None if place and (place.casefold() in _NO_PLACE or _DIRECTIONS.search(place)) else place
    # "Duplex Club" is a venue; "מנשה בן ישראל 17 , תל אביב" an address.
    is_address = bool(place and _STREET.search(place))
    project = item.get("project") or {}
    tickets = [_tier(p) for p in products if isinstance(p, dict) and p.get("product_type") in _ENTRY]
    prices = [t.price for t in tickets if t.price is not None]
    cover = clean_str(item.get("cover"))
    return ScrapedEvent(
        source=VibezScraper.name,
        source_event_id=event_id,
        url=f"{site_url}/events/{event_id}",
        title=title,
        description=html_to_text(str(item.get("description") or "")),
        start_at=start,
        end_at=_end(start, parse_iso(item.get("end_time"), item.get("timezone") or timezone)),
        venue_name=None if is_address else place,
        address=place if is_address else None,
        organizer=clean_str(project.get("name")),
        image_url=cover,
        currency=clean_str(project.get("currency")) or "ILS",
        min_price=min(prices, default=None),
        max_price=max(prices, default=None),
        tickets=tickets,
        is_sold_out=bool(tickets) and all(t.status is TierStatus.SOLD_OUT for t in tickets),
    )


def _end(start: datetime, end: datetime | None) -> datetime | None:
    """An "end" weeks or a year away is the sale window of a recurring series, not the event's."""
    return end if end is None or end - start <= MAX_SPAN else None


def _tier(product: dict[str, Any]) -> TicketTier:
    label = clean_str(product.get("client_label")) or ""
    if _SOLD_OUT.search(label) or product.get("left") == 0:
        status = TierStatus.SOLD_OUT
    elif product.get("status") != "active" or label:  # "Sale ended", "Not available"
        status = TierStatus.CLOSED
    else:
        status = TierStatus.ON_SALE
    price = to_decimal(product.get("price"))
    return TicketTier(
        source_ticket_id=clean_str(product.get("id")),
        name=clean_str(product.get("name")) or "כרטיס",
        description=clean_str(product.get("description")),
        price=price if price is None else price.quantize(Decimal("0.01")),
        status=status,
        sale_start=parse_iso(product.get("sales_start")),
        sale_end=parse_iso(product.get("sales_end")),
    )
