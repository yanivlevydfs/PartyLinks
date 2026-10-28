"""Be'er Sheva Theatre (b7t.co.il) - repertoire, guest shows and children's theatre.

Sells on a Smarticket tenant (``b7t``) whose ``/api/shows`` and calendar feed are both empty, so
the generic Smarticket source finds nothing. robots.txt allows everything. Listing: the theatre's
WordPress home page and schedule page (``/לוח-הצגות/``) link every bookable performance as
``b7t.smarticket.co.il/(iframe/)event/<id>`` (~40). Details: each performance's iframe page has a
schema.org ``Event`` with name, start/end, hall, street address, one price, availability and the
artwork; the page title adds the show's category ("תיאטרון בחירה").
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from urllib.parse import quote

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
)
from partylinks.scrapers.base import BaseScraper, SourceSettings

_EVENT_ID = re.compile(r"smarticket\.co\.il/+(?:iframe/)?event/(\d+)")
_TITLE = re.compile(r"<title>(.*?)</title>", re.DOTALL)
VENUE = "תיאטרון באר שבע"


class BeerShevaTheatreSettings(SourceSettings):
    listing_urls: list[str]
    tenant_url: str  # https://b7t.smarticket.co.il


class BeerShevaTheatreScraper(BaseScraper[BeerShevaTheatreSettings]):
    name = "beershevatheatre"
    settings_model = BeerShevaTheatreSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        ids: dict[str, None] = {}
        for url in self.settings.listing_urls:
            ids.update(dict.fromkeys(performance_ids(await self.get_text(quote(url, safe=":/")))))
        self.log.info("listed %d performances", len(ids))
        async for event in self.stream_isolated(list(ids), self._fetch, label=lambda i: i):
            yield event

    async def _fetch(self, performance_id: str) -> ScrapedEvent | None:
        base = self.settings.tenant_url
        page = await self.get_text(f"{base}/iframe/event/{performance_id}")
        return parse_performance(page, performance_id, base, self.timezone)


def performance_ids(page_html: str) -> list[str]:
    return list(dict.fromkeys(_EVENT_ID.findall(page_html)))


def parse_performance(page_html: str, performance_id: str, base: str, timezone: str) -> ScrapedEvent | None:
    """One performance from its iframe page's Event JSON-LD; None when cancelled."""
    event = next((n for n in jsonld_nodes(page_html) if "event" in schema_types(n)), None)
    if event is None:
        raise ParseError(f"b7t {performance_id}: no Event JSON-LD")
    if str(event.get("eventStatus") or "").endswith("EventCancelled"):
        return None
    title, start = clean_str(event.get("name")), parse_iso(event.get("startDate"), timezone)
    if not title or not start:
        raise ParseError(f"b7t {performance_id}: Event without name or start")
    place = event.get("location") if isinstance(event.get("location"), dict) else {}
    offer = event.get("offers") if isinstance(event.get("offers"), dict) else {}
    price = to_decimal(offer.get("price"))
    sold_out = str(offer.get("availability") or "").endswith("SoldOut")
    page_title = _TITLE.search(page_html)
    # "<show> • תיאטרון באר שבע • <category>"
    parts = [p.strip() for p in (page_title[1] if page_title else "").split("•")]
    category = clean_str(parts[2]) if len(parts) > 2 else None
    images = event.get("image") if isinstance(event.get("image"), list) else [event.get("image")]
    return ScrapedEvent(
        source=BeerShevaTheatreScraper.name,
        source_event_id=performance_id,
        url=clean_str(offer.get("url")) or f"{base}/event/{performance_id}",
        title=title,
        description=html_to_text(event.get("description")),
        start_at=start,
        time_known=has_time(event.get("startDate")),
        end_at=parse_iso(event.get("endDate"), timezone),
        venue_name=VENUE,
        address=clean_str(place.get("streetAddress")),
        city="באר שבע",
        event_types=["theatre", *([category] if category else [])],
        organizer=VENUE,
        images=[i.replace("//uploads/", "/uploads/") for i in images if isinstance(i, str)],
        min_price=price,
        max_price=price,
        is_sold_out=sold_out,
        tickets=[TicketTier(name="כרטיס", price=price, status=TierStatus.SOLD_OUT if sold_out else TierStatus.ON_SALE)],
        extra={"hall": clean_str(place.get("name"))},
    )
