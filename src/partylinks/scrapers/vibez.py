"""Vibez (vibez.io) - ticketing for communities and party producers: raves, ecstatic dance, club nights.

A Next.js app on a public Rails JSON API (``api.vibez.io/api/v102/buyer``); robots.txt is absent.
Listing: ``/events?page=N`` gives 20 upcoming events a page (5 pages, ~90): name, place, start/end,
cover and producer. Details: ``/events/{id}`` adds the description (HTML), and
``/events/{id}/products`` the ticket tiers with price, stock and sale window. The site has no
categories (``tags`` is always empty), so genres come from the name and description.

Unlisted events (a producer's event out of the public listing, sold through a link it posts, e.g. a
Facebook ad) are read too: ``/projects/{id}/events`` gives each producer's whole catalogue, past ones
included, so every producer seen in the listing (~40) adds its coming events (one request each;
past ones are left out before any detail request).
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from decimal import Decimal
from functools import lru_cache
from importlib import resources
from typing import Any

import httpx
from pydantic import Field

from partylinks.countries import country_code
from partylinks.enrichment import UNKNOWN_COUNTRY
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
                payload = await self.get_json(f"{s.api_url}/events", params={"page": page}, expect=dict)
                items = payload.get("events") or []
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 400:  # "expected :page in 1..5": past the last page
                    break
                raise
            if not items:
                break
            # The listing keeps an event its producer never closed (a September date in November).
            listed.update(
                (i["id"], i) for i in items if isinstance(i, dict) and i.get("id") and _coming(i, self.timezone)
            )
        if not listed:
            raise ParseError("the event listing is empty")
        self.log.debug("listed %d events", len(listed))
        projects = {pid for i in listed.values() if (pid := clean_str((i.get("project") or {}).get("id")))}
        for pid in sorted(projects):
            payload = await self.optional(
                f"project {pid}", self.get_json(f"{s.api_url}/projects/{pid}/events", expect=dict)
            )
            for item in (payload or {}).get("events") or []:
                if (
                    isinstance(item, dict)
                    and item.get("id")
                    and item["id"] not in listed
                    and _coming(item, self.timezone)
                ):
                    listed[item["id"]] = item
        self.log.debug("%d events with the producers' unlisted ones", len(listed))
        async for event in self.stream_isolated(list(listed), self._fetch_event, label=lambda i: i):
            yield event

    async def _fetch_event(self, event_id: str) -> ScrapedEvent | None:
        s = self.settings
        detail = (await self.get_json(f"{s.api_url}/events/{event_id}")).get("event") or {}
        products = (await self.get_json(f"{s.api_url}/events/{event_id}/products")).get("products") or []
        return parse_event(detail, products, s.site_url, self.timezone)


def _coming(item: dict[str, Any], timezone: str) -> bool:
    """Not over yet (its end, else its start, is ahead): past catalogue rows get no detail request."""
    zone = item.get("timezone") or timezone
    start, end = parse_iso(item.get("start_time"), zone), parse_iso(item.get("end_time"), zone)
    if start and end and end - start > MAX_SPAN:  # a placeholder end weeks on (parse_event drops it too)
        end = None
    when = end or start
    return bool(when and when > datetime.now(when.tzinfo))


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
    country = _country(place, item.get("timezone"), (item.get("project") or {}).get("currency"))
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
        country=country,
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


@lru_cache(maxsize=1)
def _zone_countries() -> dict[str, str]:
    """IANA time zone -> ISO country, from tzdata's zone.tab ("Asia/Bangkok" -> "TH")."""
    table = resources.files("tzdata.zoneinfo").joinpath("zone.tab").read_text(encoding="utf-8")
    rows = (line.split("	") for line in table.splitlines() if line and not line.startswith("#"))
    return {row[2]: row[0] for row in rows if len(row) >= 3}


def _country(place: str | None, zone: Any, currency: Any) -> str | None:
    """ISO country of an event abroad, None in Israel. Vibez gives no country. A place that names one
    wins ("Greece", "..., Thailand"); else the time zone, but only for a price not in shekels: some
    producers leave a wrong zone (a Tel Aviv street set to "Indian/Mahe", Greece to Moscow)."""
    for part in reversed([p.strip() for p in (place or "").split(",") if p.strip()]):
        if (code := country_code(part)) != UNKNOWN_COUNTRY:
            break
    else:
        code = _zone_countries().get(str(zone or "")) if str(currency or "ILS").upper() != "ILS" else None
    return None if code in (None, "IL", "PS") else code  # Asia/Hebron (PS) is listed as Israel


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
