"""Luma (luma.com) - RSVP pages for tech meetups, hi-tech conferences, startup and community events.

Public JSON API at ``api.lu.ma``. Listing: ``/discover/get-paginated-events`` near a point
(``latitude``/``longitude``) pages with a cursor through every upcoming event in range; a
point in Tel Aviv already reaches Jerusalem and Jordan, so a few centers cover the country and
events abroad are dropped. Details: ``/event/get?event_api_id`` gives the description (a
ProseMirror document), the place, the categories ("ai", "tech", "crypto"...) and the ticket
types with prices in cents (often USD). Most events are free with host approval. The street
address is shown only for some events (``mode: shown``); for the rest only the city is known
and the given coordinate is deliberately blurred, so it is not used.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from partylinks.models import ScrapedEvent, TicketTier, TierStatus
from partylinks.parsing import ParseError, clean_str, parse_iso, to_float
from partylinks.scrapers.base import BaseScraper, SourceSettings

# Luma categories that mark a tech / hi-tech event; given as the "hi-tech" genre word.
_TECH_CATEGORIES = {"ai", "tech", "crypto"}
_BLOCKS = {"paragraph", "heading", "list_item", "blockquote", "code_block"}


class Center(BaseModel):
    latitude: float
    longitude: float


class LumaSettings(SourceSettings):
    api_url: str
    site_url: str
    centers: list[Center]
    page_size: int = Field(50, ge=1, le=50)
    max_pages: int = Field(20, ge=1)


class LumaScraper(BaseScraper[LumaSettings]):
    name = "luma"
    settings_model = LumaSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        ids: dict[str, None] = {}
        for center in self.settings.centers:
            ids.update(dict.fromkeys(await self._list(center)))
        self.log.info("listed %d upcoming events", len(ids))
        async for event in self.stream_isolated(list(ids), self._fetch_event, label=lambda i: i):
            yield event

    async def _list(self, center: Center) -> list[str]:
        s, ids, cursor = self.settings, [], None
        for _ in range(s.max_pages):
            params: dict[str, Any] = {
                "latitude": center.latitude,
                "longitude": center.longitude,
                "pagination_limit": s.page_size,
            }
            if cursor:
                params["pagination_cursor"] = cursor
            page = await self.get_json(s.api_url + "/discover/get-paginated-events", expect=dict, params=params)
            ids += listed_ids(page)
            cursor = page.get("next_cursor")
            if not page.get("has_more") or not cursor:
                return ids
        self.log.warning("listing truncated at max_pages=%d", s.max_pages)
        return ids

    async def _fetch_event(self, event_id: str) -> ScrapedEvent | None:
        s = self.settings
        detail = await self.get_json(s.api_url + "/event/get", expect=dict, params={"event_api_id": event_id})
        return parse_event(detail, s.site_url, self.timezone)


def listed_ids(page: dict[str, Any]) -> list[str]:
    """Ids of the in-person events in Israel on one listing page."""
    ids = []
    for entry in page.get("entries") or []:
        event = entry.get("event") or {}
        geo = event.get("geo_address_info") or {}
        if event.get("location_type") == "offline" and geo.get("country_code") == "IL" and entry.get("api_id"):
            ids.append(entry["api_id"])
    return ids


def parse_event(detail: dict[str, Any], site_url: str, timezone: str) -> ScrapedEvent | None:
    """One event from ``/event/get``; None when it is online or abroad."""
    event = detail.get("event") or {}
    geo = event.get("geo_address_info") or {}
    if event.get("location_type") != "offline" or geo.get("country_code") != "IL":
        return None
    title, start = clean_str(event.get("name")), parse_iso(event.get("start_at"), timezone)
    if not title or not start or not event.get("api_id"):
        raise ParseError(f"{event.get('api_id')}: event without id, name or start")
    hebrew = (geo.get("localized") or {}).get("iw") or {}
    shown = geo.get("mode") == "shown"
    point = (geo.get("place_coordinate") or {}) if shown else {}
    calendar = detail.get("calendar") or {}
    hosts = [n for h in detail.get("hosts") or [] if (n := clean_str(h.get("name")))]
    categories = [c for c in detail.get("categories") or [] if isinstance(c, dict)]
    tickets = [_tier(t) for t in detail.get("ticket_types") or [] if not t.get("is_hidden")]
    prices = [t.price for t in tickets if t.price is not None]
    return ScrapedEvent(
        source=LumaScraper.name,
        source_event_id=event["api_id"],
        url=f"{site_url}/{event.get('url') or event['api_id']}",
        title=title,
        description=mirror_text(detail.get("description_mirror")),
        start_at=start,
        end_at=parse_iso(event.get("end_at"), timezone),
        venue_name=clean_str(geo.get("description")) if shown else None,
        address=clean_str(hebrew.get("address") or geo.get("address")) if shown else None,
        city=clean_str(hebrew.get("city") or geo.get("city")),
        latitude=to_float(point.get("latitude")),
        longitude=to_float(point.get("longitude")),
        source_genres=["hi-tech"] if any(c.get("slug") in _TECH_CATEGORIES for c in categories) else [],
        event_types=[n for c in categories if (n := clean_str(c.get("name")))],
        # A personal calendar is named "Personal": the hosts are the organizer then.
        organizer=clean_str(calendar.get("name"))
        if not calendar.get("personal_user_api_id")
        else ", ".join(hosts) or None,
        images=[i for i in (event.get("cover_url"),) if i],
        currency=next((t.currency for t in tickets if t.price), "ILS"),
        min_price=min(prices, default=None),
        max_price=max(prices, default=None),
        is_sold_out=bool(detail.get("sold_out")),
        tickets=tickets,
    )


def _tier(ticket: dict[str, Any]) -> TicketTier:
    free = ticket.get("type") == "free"
    cents = ticket.get("cents")
    remaining = ticket.get("spots_remaining")
    return TicketTier(
        source_ticket_id=clean_str(ticket.get("api_id")),
        name=clean_str(ticket.get("name")) or "Standard",
        price=Decimal(0) if free else Decimal(cents) / 100 if isinstance(cents, int) else None,
        currency=(clean_str(ticket.get("currency")) or "ILS").upper(),
        is_free=free,
        status=TierStatus.SOLD_OUT
        if remaining == 0
        else TierStatus.CLOSED
        if ticket.get("is_disabled")
        else TierStatus.ON_SALE,
        remaining=remaining if isinstance(remaining, int) else None,
        sale_start=parse_iso(ticket.get("valid_start_at")),
        sale_end=parse_iso(ticket.get("valid_end_at")),
    )


def mirror_text(doc: Any) -> str | None:
    """Plain text of a ProseMirror document: one line per block, hard breaks as newlines."""
    lines: list[str] = []

    def inline(node: dict[str, Any]) -> str:
        if node.get("type") == "text":
            return str(node.get("text") or "")
        if node.get("type") == "hard_break":
            return "\n"
        return "".join(inline(c) for c in node.get("content") or [] if isinstance(c, dict))

    def walk(node: dict[str, Any]) -> None:
        children = [c for c in node.get("content") or [] if isinstance(c, dict)]
        if node.get("type") in _BLOCKS and not any(
            c.get("type") in _BLOCKS | {"bullet_list", "ordered_list"} for c in children
        ):
            lines.append(inline(node).strip())
        else:
            for child in children:
                walk(child)

    if isinstance(doc, dict):
        walk(doc)
    return "\n".join(line for line in lines if line) or None
