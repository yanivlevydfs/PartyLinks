"""Leaan (leaan.co.il) - ticketing site for shows, stand-up, kids' shows and sport (Next.js).

No JSON API is used. Listing: the homepage's ``__NEXT_DATA__`` holds the search index
(``props.pageProps.initialState.search.events``): every listed event with name, epoch
start/end, venue (name, street, city), categories, labels, starting price, artwork and
description, so one request returns almost the whole catalogue. Backfill: ``sitemap.xml``
lists ``/events/{slug}/{id}`` pages (the slug is ignored); ids missing from the homepage
index are read from their event page (``props.pageProps.data``, same shape plus ``tickets``).

Skipped: events already started, season tickets (``subscription``) and the zero-priced
"special" refund / compensation items sport clubs sell to subscribers. The listing has only
a starting price (``min_price``); ticket tiers exist only on backfilled event pages.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import (
    ParseError,
    clean_str,
    from_unix,
    html_to_text,
    next_data,
    slugify,
    to_decimal,
    to_int,
    zone,
)
from partylinks.scrapers.base import BaseScraper, SourceSettings

_MIN_AGE = re.compile(r"^(\d{1,2})\+$")


class LeaanSettings(SourceSettings):
    site_url: str
    listing_path: str
    event_page_path: str  # "{slug}" and "{event_id}" placeholders
    sitemap_path: str = ""  # empty = no backfill of events missing from the listing


class LeaanScraper(BaseScraper[LeaanSettings]):
    name = "leaan"
    settings_model = LeaanSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        page_url = s.site_url + s.event_page_path
        now = datetime.now(timezone.utc)
        raws = listing_events(await self.get_text(s.site_url + s.listing_path))
        if not raws:
            raise ParseError("no events in the homepage search index")
        self.log.info("listed %d events", len(raws))
        for raw in raws:
            try:
                if (event := parse_event(raw, page_url, self.timezone, now)) is not None:
                    yield event
            except Exception as exc:  # noqa: BLE001 - one bad item never drops the rest
                self.record_failure(f"event {raw.get('id')}", exc)

        if not s.sitemap_path:
            return
        sitemap = await self.optional("sitemap", self.get_text(s.site_url + s.sitemap_path)) or ""
        listed = {str(r.get("id")) for r in raws}
        missing = [i for i in sitemap_event_ids(sitemap, s.event_page_path) if i not in listed]
        self.log.info("%d sitemap events missing from the listing", len(missing))

        async def fetch(event_id: str) -> ScrapedEvent | None:
            url = page_url.format(slug="event", event_id=event_id)
            return parse_event(event_page_data(await self.get_text(url)), page_url, self.timezone, now)

        async for event in self.stream_isolated(missing, fetch, label=lambda i: f"event {i}"):
            yield event


def listing_events(page_html: str) -> list[dict[str, Any]]:
    state = next_data(page_html).get("props", {}).get("pageProps", {}).get("initialState") or {}
    events = (state.get("search") or {}).get("events")
    return [e for e in events if isinstance(e, dict)] if isinstance(events, list) else []


def event_page_data(page_html: str) -> dict[str, Any]:
    data = next_data(page_html).get("props", {}).get("pageProps", {}).get("data")
    if not isinstance(data, dict):
        raise ParseError("event page has no data")
    return data


def sitemap_event_ids(sitemap: str, event_page_path: str) -> list[str]:
    """Ids of the event pages in the sitemap, in order, without duplicates."""
    prefix = re.escape(event_page_path.split("{", 1)[0])
    return list(dict.fromkeys(re.findall(rf"<loc>[^<]*{prefix}[^/<]+/(\d+)/?\s*</loc>", sitemap)))


def parse_event(raw: dict[str, Any], page_url_template: str, timezone: str, now: datetime) -> ScrapedEvent | None:
    """The event, or None for items that are not upcoming events (past, season tickets, refunds)."""
    event_id, title = raw.get("id"), clean_str(raw.get("event_name") or raw.get("name"))
    if not event_id or not title:
        raise ParseError("event id/name missing")
    price = to_decimal(raw.get("starting_price"))
    start = from_unix(raw.get("event_start"))
    if (
        to_int(raw.get("subscription"))
        or (to_int(raw.get("special_event")) and not price)
        or (start is not None and start < now)
    ):
        return None
    tz = zone(timezone)
    end = from_unix(raw.get("event_end"))
    if end is not None and start is not None and end <= start:
        end = None

    location = raw.get("location") or {}
    categories = [c for c in (raw.get("categories") or {}).values() if isinstance(c, dict)]
    genres = [clean_str(c.get("category_name")) for c in categories]
    genres += [clean_str(sub.get("name") if isinstance(sub, dict) else sub) for c in categories for sub in c.get("subcategories") or []]
    labels = [n for label in raw.get("labels") or [] if (n := clean_str(label.get("label_name")))]
    age = next((m for m in map(_MIN_AGE.match, labels) if m), None)
    images = raw.get("images") or {}
    gallery = raw.get("gallery") or {}
    artwork = [
        (images.get("desktop") or {}).get("vivenu_image"),
        (images.get("mobile") or {}).get("vivenu_image"),
        *(v for key in ("desktop", "mobile") if isinstance(gallery.get(key), dict) for v in gallery[key].values()),
        raw.get("vivenu_image"),
    ]
    sold_out = bool(raw.get("sold_out") or raw.get("is_soldout"))
    tickets = [
        TicketTier(
            source_ticket_id=clean_str(t.get("vivenu_ticket_id")),
            name=clean_str(t.get("name")) or "כרטיס",
            description=clean_str(t.get("description")),
            price=to_decimal(t.get("price")),
            is_sold_out=bool(t.get("is_soldout")),
            is_active=str(t.get("active", "1")) == "1",
        )
        for t in raw.get("tickets") or []
        if isinstance(t, dict)
    ]
    prices = [t.price for t in tickets if t.price is not None]
    single_price = price is not None and raw.get("has_multiple_prices") is False

    return ScrapedEvent(
        source=LeaanScraper.name,
        source_event_id=str(event_id),
        url=page_url_template.format(slug=slugify(title) or "event", event_id=event_id),
        title=title,
        description=html_to_text(raw.get("description")),
        start_at=start.astimezone(tz) if start else None,
        end_at=end.astimezone(tz) if end else None,
        venue_name=clean_str(location.get("name")),
        address=clean_str(location.get("street")),
        city=clean_str(location.get("city")),
        source_genres=list(dict.fromkeys(g for g in genres if g)),
        min_age=to_int(age.group(1)) if age else None,
        images=[i for i in artwork if isinstance(i, str) and i.startswith("http")],
        min_price=price if price is not None else min(prices, default=None),
        max_price=max(prices, default=price if single_price else None),
        is_sold_out=sold_out,
        tickets=tickets,
        extra={
            "purchase_url": clean_str(raw.get("redirect_url")),
            "seller_id": raw.get("seller_id"),
            "labels": labels,
            "restriction": clean_str(raw.get("restriction")),
            "sport": bool(raw.get("is_sport_event")),
            "promoted": bool(raw.get("promoted")),
        },
    )
