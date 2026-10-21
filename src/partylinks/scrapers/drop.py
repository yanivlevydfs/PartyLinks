"""Drop (tdrop.net) - self-serve ticketing for party producers: raves, house parties, festivals.

Next.js on Supabase; robots.txt allows everything. Listing: the sitemap lists every event page
(``/events/<16 hex>``, ~850 all-time) with ``lastmod`` set to the event's date, so only pages
dated from yesterday on are read (~50). Details: each page's schema.org ``Event`` JSON-LD gives
name, start/end, image, place, organizer and one ``Offer`` per ticket tier. Every offer's price
reads "0" whatever the tier costs (tiers load client-side), so prices are left unknown rather
than shown as free; tier names and availability are kept. Many raves name only a region
("מרכז הארץ", "השרון") until the location is sent to ticket holders.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import Field

from partylinks.models import ScrapedEvent, TicketTier, TierStatus
from partylinks.parsing import ParseError, clean_str, html_to_text, jsonld_nodes, parse_iso, schema_types
from partylinks.scrapers.base import BaseScraper, SourceSettings

_URL = re.compile(r"<url>\s*<loc>\s*(https://[^<\s]+/events/([0-9a-f]{8,}))\s*</loc>\s*(?:<lastmod>\s*([^<\s]+))?")
_PREFIX = re.compile(r"^\s*\[HTMLDESC\]\s*")
# A place named only for the country is no place; the site's stand-in artwork is no artwork.
_NO_PLACE = {"israel", "ישראל"}
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
        self.log.info("listed %d upcoming events", len(urls))
        async for event in self.stream_isolated(urls[: s.max_events], self._fetch_event, label=lambda u: u):
            yield event

    async def _fetch_event(self, url: str) -> ScrapedEvent | None:
        return parse_event(await self.get_text(url), url, self.timezone)


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
        end_at=parse_iso(event.get("endDate"), timezone),
        venue_name=venue,
        address=street if street != venue else None,
        city=clean_str(address.get("addressLocality")),
        country=None if country in ("", "IL") else country,
        organizer=clean_str(organizer.get("name")),
        images=[i for i in _list(event.get("image")) if isinstance(i, str) and _NO_IMAGE not in i],
        tickets=tickets,
        is_sold_out=bool(tickets) and all(t.status is TierStatus.SOLD_OUT for t in tickets),
    )


def _tier(offer: dict[str, Any]) -> TicketTier:
    """A ticket tier by name and availability; its price is not published (reads "0")."""
    sold_out = str(offer.get("availability") or "").endswith("SoldOut")
    return TicketTier(
        name=clean_str(offer.get("name")) or "כרטיס",
        status=TierStatus.SOLD_OUT if sold_out else TierStatus.ON_SALE,
        sale_start=parse_iso(offer.get("validFrom")),
        sale_end=parse_iso(offer.get("validThrough")),
    )


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value] if value else []
