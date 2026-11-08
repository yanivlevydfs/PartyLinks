"""Drop (tdrop.net) - self-serve ticketing for party producers: raves, house parties, festivals.

Next.js on Supabase; robots.txt allows everything. Listing: the sitemap lists every event page
(``/events/<16 hex>``, ~850 all-time) with ``lastmod`` set to the event's date, so only pages
dated from yesterday on are read (~50). Details: each page's schema.org ``Event`` JSON-LD gives
name, start/end, image, place, organizer and one ``Offer`` per ticket tier. Every offer's price
reads "0" whatever the tier costs, so the tiers come from the JSON the page itself loads
(``/api/event/general/<id>``: ``eventTicketTypes`` with price, discount and round status
current / soon / soldout / expired); the JSON-LD offers stay as a fallback, price unknown.
The API adds the minimum age and the site's categories (few events have one). Many raves name only a
region ("מרכז הארץ", "השרון") until the location is sent to ticket holders; the site has no city field, so
the city comes from the place's name.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import Field

from partylinks.config import RetentionSettings
from partylinks.countries import UNKNOWN_COUNTRY, country_code
from partylinks.models import ScrapedEvent, TicketTier, TierStatus
from partylinks.parsing import (
    ParseError,
    clean_str,
    has_time,
    html_to_text,
    jsonld_nodes,
    parse_iso,
    schema_types,
    to_decimal,
    to_int,
)
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.standupist import find_city

_URL = re.compile(r"<url>\s*<loc>\s*(https://[^<\s]+/events/([0-9a-f]{8,}))\s*</loc>\s*(?:<lastmod>\s*([^<\s]+))?")
_PREFIX = re.compile(r"^\s*\[HTMLDESC\]\s*")
# A place named only for the country is no place; the site's stand-in artwork is no artwork.
_NO_PLACE = {"israel", "ישראל", "to be announced"}
_NO_IMAGE = "/defaults/"


class DropSettings(SourceSettings):
    site_url: str
    sitemap_path: str
    max_events: int = Field(300, ge=1)


class DropScraper(BaseScraper[DropSettings]):
    name = "drop"
    settings_model = DropSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        urls = upcoming_urls(await self.get_text(s.site_url + s.sitemap_path), datetime.now(UTC) - timedelta(days=1))
        if len(urls) > s.max_events:
            self.log.warning("listing truncated at max_events=%d (%d found)", s.max_events, len(urls))
        self.log.debug("listed %d upcoming events", len(urls))
        async for event in self.stream_isolated(urls[: s.max_events], self._fetch_event, label=lambda u: u):
            if is_current(event.start_at, event.end_at, RetentionSettings(), self.timezone):
                yield event

    async def _fetch_event(self, url: str) -> ScrapedEvent | None:
        event = parse_event(await self.get_text(url), url, self.timezone)
        if event is None:
            return None
        api = f"{self.settings.site_url}/api/event/general/{event.source_event_id}"
        data = await self.optional(f"tickets {event.source_event_id}", self.get_json(api, expect=dict))
        return with_api_tickets(event, data or {})


def upcoming_urls(sitemap: str, since: datetime) -> list[str]:
    """Event pages whose ``lastmod`` (the event date) is ``since`` or later; undated ones too."""
    urls: dict[str, None] = {}
    for url, _, lastmod in _URL.findall(sitemap):
        day = parse_iso(lastmod, "UTC")
        if day is None or day >= since:
            urls[url] = None
    return list(urls)


def parse_event(page_html: str, url: str, timezone: str) -> ScrapedEvent | None:
    """The page's ``Event``; None when cancelled."""
    event = next((n for n in jsonld_nodes(page_html) if "event" in schema_types(n)), None)
    if event is None:
        raise ParseError(f"{url}: no Event JSON-LD")
    if str(event.get("eventStatus") or "").endswith("EventCancelled"):
        return None
    title, start = clean_str(event.get("name")), parse_iso(event.get("startDate"), timezone)
    if not title or not start:
        raise ParseError(f"{url}: Event without name or start")
    location = event.get("location") if isinstance(event.get("location"), dict) else {}
    address = location.get("address") if isinstance(location.get("address"), dict) else {}
    organizer = event.get("organizer") if isinstance(event.get("organizer"), dict) else {}
    offers = [o for o in _list(event.get("offers")) if isinstance(o, dict)]
    tickets = [_tier(o) for o in offers]
    venue = clean_str(location.get("name"))
    venue = None if venue and venue.casefold() in _NO_PLACE else venue
    street = clean_str(address.get("streetAddress"))
    country = (clean_str(address.get("addressCountry")) or "").upper()
    return ScrapedEvent(
        source=DropScraper.name,
        source_event_id=url.rstrip("/").rsplit("/", 1)[-1],
        url=clean_str(event.get("url")) or url,
        title=title,
        description=html_to_text(_PREFIX.sub("", str(event.get("description") or ""))),
        start_at=start,
        time_known=has_time(event.get("startDate")),
        end_at=parse_iso(event.get("endDate"), timezone),
        venue_name=venue,
        address=street if street != venue else None,
        city=clean_str(address.get("addressLocality")) or find_city(street, venue),
        country=_abroad(venue) if country in ("", "IL") else country,
        organizer=clean_str(organizer.get("name")),
        images=[i for i in _list(event.get("image")) if isinstance(i, str) and _NO_IMAGE not in i],
        tickets=tickets,
        is_sold_out=bool(tickets) and all(t.status is TierStatus.SOLD_OUT for t in tickets),
    )


def _abroad(venue: str | None) -> str | None:
    """ "Hilltop - Goa - India": a place named with its country last; the site gives no other sign of abroad."""
    parts = re.split(r"\s+-\s+|\s*,\s*", venue or "")
    code = country_code(parts[-1]) if len(parts) > 1 else None
    return None if code in (None, "IL", UNKNOWN_COUNTRY) else code


def _tier(offer: dict[str, Any]) -> TicketTier:
    """A ticket tier by name and availability; its price is not published (reads "0")."""
    sold_out = str(offer.get("availability") or "").endswith("SoldOut")
    return TicketTier(
        name=clean_str(offer.get("name")) or "כרטיס",
        status=TierStatus.SOLD_OUT if sold_out else TierStatus.ON_SALE,
        sale_start=parse_iso(offer.get("validFrom")),
        sale_end=parse_iso(offer.get("validThrough")),
    )


_STATUS = {"soon": TierStatus.COMING_SOON, "soldout": TierStatus.SOLD_OUT, "expired": TierStatus.CLOSED}


def with_api_tickets(event: ScrapedEvent, data: dict[str, Any]) -> ScrapedEvent:
    """``event`` with the event API's categories, minimum age and priced tiers (JSON-LD tiers if it lists none)."""
    update: dict[str, Any] = {
        "source_genres": [
            c["name"].lower() for c in _list(data.get("categories")) if isinstance(c, dict) and c.get("name")
        ],
        "min_age": to_int(data.get("minAge")),
    }
    tickets = [_api_tier(t) for t in _list(data.get("eventTicketTypes")) if isinstance(t, dict)]
    if tickets:
        update |= {"tickets": tickets, "is_sold_out": all(t.status is TierStatus.SOLD_OUT for t in tickets)}
    return event.model_copy(update=update)


def _api_tier(t: dict[str, Any]) -> TicketTier:
    # The page's own rule: a discount counts only when it is above 0 and below the price.
    price, discount = to_decimal(t.get("price")), to_decimal(t.get("discountedPrice"))
    discounted = price is not None and discount is not None and 0 < discount < price
    quantity, sold = to_int(t.get("quantity")), to_int(t.get("soldQuantity")) or 0
    return TicketTier(
        source_ticket_id=clean_str(t.get("id")),
        name=clean_str(t.get("name")) or "כרטיס",
        price=discount if discounted else price,
        original_price=price if discounted else None,
        currency=clean_str(t.get("currency")) or "ILS",
        status=_STATUS.get(str(t.get("status")), TierStatus.ON_SALE),
        remaining=max(quantity - sold, 0) if quantity is not None else None,
        sale_start=parse_iso(t.get("sellStartDateTimeWithTimezone")),
        sale_end=parse_iso(t.get("sellEndDateTimeWithTimezone")),
    )


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value] if value else []
