"""Makore (makore.co.il, "מה קורה") - an events guide for all of Israel.

Next.js site; robots.txt allows everything but /admin. Listing: each configured category page
(``/browse/category/<name>``) carries a schema.org ``ItemList`` of ~28 ``Event`` items in its
JSON-LD, page N at ``?pages=N``; the meta description states the category total ("239 אירועי
חיי לילה..."), so every page is fetched in parallel. Items already have name, description,
start/end, image, venue with street, city and coordinates, and the box office link and price
(``offers``). ~3 in 4 items have no price there (Nov 2026): the event's own page holds the site's
reading of the seller's text in its React Server Components state (``event.pricing``: fixed, from,
range, ticket types, free, or unknown with the raw text), so that page is read for those; what is
still missing (price, picture) comes from the seller's page (``seller_offers``). ``source_event_id`` is the short hex id the
event URL ends with (``/event/<slug>--25f6fc42``, or ``/event/25f6fc42``). Makore aggregates other box offices, so most
events are also listed by another source; dedup merges them.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from datetime import time
from typing import Any
from urllib.parse import unquote

import httpx
from pydantic import Field
from selectolax.lexbor import LexborHTMLParser

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import (
    ParseError,
    clean_str,
    has_time,
    html_to_text,
    jsonld_nodes,
    parse_iso,
    schema_types,
    to_decimal,
    zone,
)
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.seller_offers import SellerLookup, SellerLookupSettings, free_tier, priced, with_tickets
from partylinks.scrapers.text_prices import text_tickets

_EVENT_ID = re.compile(r"(?:--|/event/)([0-9a-f]{6,})/?$")
_TOTAL = re.compile(r"^\s*([\d,]+)\s")
# One RSC row: self.__next_f.push([1,"<JSON string>"])
_FLIGHT = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)')


class MakoreSettings(SourceSettings, SellerLookupSettings):
    site_url: str
    # Category pages read, e.g. /browse/category/חיי-לילה (nightlife), /browse/category/הופעות-חיות.
    categories: list[str] = Field(min_length=1)
    page_size: int = Field(28, ge=1)
    max_pages: int = Field(200, ge=1)
    # Read the event's own page for an item listed without a price (one request each).
    page_prices: bool = True


class MakoreScraper(BaseScraper[MakoreSettings]):
    name = "makore"
    settings_model = MakoreSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        seen: set[str] = set()
        incomplete: list[ScrapedEvent] = []  # without a price or a picture: completed below
        self._pages_read: set[str] = set()  # event pages already read (series dates)
        retention = RetentionSettings()
        series: dict[str, str] = {}  # series page -> category name
        for category in self.settings.categories:
            items = await self.optional(category, self._category(category))
            name = unquote(category.rstrip("/").rsplit("/", 1)[-1])
            for item in items or []:
                url = str(item.get("url") or "")
                if "/series/" in url:  # one show's many dates: a tile with no date of its own
                    series.setdefault(url, name)
                    continue
                try:
                    event = parse_item(item, name, self.timezone)
                except Exception as exc:  # noqa: BLE001 - isolate per-item failures
                    self.record_failure(url, exc)
                    continue
                # Past and broken far dates (a category tile stamped 2031-06-30, a 2038-01-19 epoch) are left out.
                if event is None or not is_current(
                    event.start_at, event.end_at, retention, self.timezone, time_known=event.time_known
                ):
                    continue
                if event.source_event_id not in seen:
                    seen.add(event.source_event_id)
                    if priced(event) and event.images:
                        yield event
                    else:
                        incomplete.append(event)
        # A series page lists its dates' event pages; each page's JSON-LD Event is one listing item.
        dated: dict[str, str] = {}
        async for links in self.stream_isolated(list(series.items()), self._series_links, label=lambda p: p[0]):
            for link, name in links:
                found = _EVENT_ID.search(link)
                if found and found.group(1) not in seen:
                    dated.setdefault(link, name)
        async for event in self.stream_isolated(list(dated.items()), self._event_page, label=lambda p: p[0]):
            if (
                event is None
                or event.source_event_id in seen
                or not is_current(event.start_at, event.end_at, retention, self.timezone, time_known=event.time_known)
            ):
                continue
            seen.add(event.source_event_id)
            incomplete.append(event)
        sellers = SellerLookup(self, self.settings)
        async for event in self.stream_isolated(
            incomplete, lambda e: self._complete(e, sellers), label=lambda e: str(e.url)
        ):
            yield event
        self.log.info(
            "listed %d events (%d series, %d series dates); %d without a price or picture, seller filled %s",
            len(seen),
            len(series),
            len(dated),
            len(incomplete),
            dict(sellers.filled),
        )

    async def _complete(self, event: ScrapedEvent, sellers: SellerLookup) -> ScrapedEvent:
        """The event's own page for a price, then its seller; the event as listed when both fail."""
        if self.settings.page_prices and not priced(event) and event.source_event_id not in self._pages_read:
            try:
                tickets = page_tickets(await self.get_text(str(event.url)), event.source_event_id)
            except Exception as exc:  # noqa: BLE001 - the listing's data is saved anyway
                self.log.debug("event page %s: %s", event.url, exc)
                tickets = []
            if tickets:
                event = with_tickets(event, tickets)
        return await sellers.fill(event)

    async def _series_links(self, page: tuple[str, str]) -> list[tuple[str, str]]:
        url, name = page
        return [(link, name) for link in series_links(await self.get_text(url))]

    async def _event_page(self, page: tuple[str, str]) -> ScrapedEvent | None:
        url, name = page
        page_html = await self.get_text(url)
        nodes = [n for n in jsonld_nodes(page_html) if "event" in schema_types(n)]
        if not nodes:
            raise ParseError(f"{url}: no Event JSON-LD")
        event = parse_item(nodes[0], name, self.timezone)
        if event is None or priced(event):
            return event
        tickets = page_tickets(page_html, event.source_event_id)
        self._pages_read.add(event.source_event_id)
        return with_tickets(event, tickets) if tickets else event

    async def _category(self, path: str) -> list[dict[str, Any]]:
        s = self.settings
        url = s.site_url + path

        async def page(index: int) -> tuple[list[dict[str, Any]], int | None]:
            try:
                page_html = await self.get_text(url, params={"pages": index + 1} if index else None)
            except httpx.HTTPStatusError as exc:
                # The stated total counts more than the pages hold: a page past the last is a 404.
                if index and exc.response.status_code == 404:
                    return [], None
                raise
            return parse_listing(page_html), listing_total(page_html)

        return await self.paginate(page, s.page_size, s.max_pages)


def parse_listing(page_html: str) -> list[dict[str, Any]]:
    """The ``Event`` items of the page's ``ItemList``."""
    lists = [n for n in jsonld_nodes(page_html) if "itemlist" in schema_types(n)]
    if not lists:
        raise ParseError("no ItemList on the category page")
    return [
        e["item"]
        for e in lists[0].get("itemListElement") or []
        if isinstance(e, dict) and isinstance(e.get("item"), dict)
    ]


def series_links(page_html: str) -> list[str]:
    """The event pages a series page lists (its ``ItemList``), once each."""
    lists = [n for n in jsonld_nodes(page_html) if "itemlist" in schema_types(n)]
    items = lists[0].get("itemListElement") or [] if lists else []
    return list(dict.fromkeys(u for e in items if isinstance(e, dict) and (u := clean_str(e.get("url")))))


def listing_total(page_html: str) -> int | None:
    """The category's event count, from the meta description ("3477 הופעות חיות בישראל...")."""
    meta = LexborHTMLParser(page_html).css_first('meta[name="description"]')
    found = _TOTAL.match(meta.attributes.get("content") or "") if meta else None
    return int(found.group(1).replace(",", "")) if found else None


def page_state(page_html: str, short_id: str) -> dict[str, Any] | None:
    """The event object of the page's RSC state (``{"event": {"id": "<full id>", "pricing": ...}}``)."""
    payload = "".join(json.loads(row) for row in _FLIGHT.findall(page_html))
    decoder = json.JSONDecoder()
    for found in re.finditer(r'\{"event":\{"id":"' + re.escape(short_id), payload):
        try:
            state = decoder.raw_decode(payload, found.start())[0]["event"]
        except (ValueError, KeyError, TypeError):
            continue
        if isinstance(state, dict):
            return state
    return None


def page_tickets(page_html: str, short_id: str) -> list[TicketTier]:
    """The price the event page states (``pricing`` kinds of the site's own price reader)."""
    state = page_state(page_html, short_id) or {}
    pricing = state.get("pricing") if isinstance(state.get("pricing"), dict) else {}
    kind, currency = pricing.get("kind"), clean_str(pricing.get("currency")) or "ILS"

    def tier(name: str, amount: Any) -> list[TicketTier]:
        price = to_decimal(amount)
        return [TicketTier(name=name, price=price, currency=currency, is_free=price == 0)] if price is not None else []

    if kind == "free" or state.get("isFree") is True:
        return [free_tier()]
    if kind == "fixed":
        return tier("כרטיס", pricing.get("amount"))
    if kind == "starting_from":
        return tier("החל מ", pricing.get("min"))
    if kind == "range":
        return tier("כרטיס", pricing.get("min")) + tier("כרטיס", pricing.get("max"))
    if kind == "ticket_types":
        rows = [t for t in pricing.get("tickets") or [] if isinstance(t, dict)]
        return [x for t in rows for x in tier(clean_str(t.get("label")) or "כרטיס", t.get("amount"))]
    # "unknown": the seller's own words, when the reader could not file them ("מחיר כרטיס: 90 ש\"ח").
    return text_tickets(clean_str(pricing.get("rawText")))


def parse_item(item: dict[str, Any], category: str, timezone: str) -> ScrapedEvent | None:
    """One listing item; None when cancelled."""
    if str(item.get("eventStatus") or "").endswith(("EventCancelled", "EventPostponed")):
        return None
    url = clean_str(item.get("url")) or ""
    found = _EVENT_ID.search(url)
    title, start = (
        clean_str(item.get("name")),
        parse_iso(item.get("startDate"), timezone),
    )
    if not found or not title or not start:
        raise ParseError(f"{url or '?'}: item without id, name or start")
    # In UTC, "00:00 here" reads 22:00: enrichment would take that for an hour and show 00:00 after all.
    start = start.astimezone(zone(timezone))
    location = item.get("location") if isinstance(item.get("location"), dict) else {}
    address = location.get("address") if isinstance(location.get("address"), dict) else None
    geo = location.get("geo") if isinstance(location.get("geo"), dict) else {}
    offer = item.get("offers") if isinstance(item.get("offers"), dict) else {}
    price = to_decimal(offer.get("price"))
    city = clean_str((address or {}).get("addressLocality"))
    venue = clean_str(location.get("name"))
    if venue and city and venue.endswith(", " + city):  # "בית רדיקל, תל אביב-יפו"
        venue = venue.removesuffix(", " + city)
    country = (clean_str((address or {}).get("addressCountry")) or "").upper()
    images = [i for i in item.get("image") or [] if isinstance(i, str)]
    return ScrapedEvent(
        source=MakoreScraper.name,
        source_event_id=found.group(1),
        url=url,
        title=title,
        description=html_to_text(item.get("description")),
        start_at=start,
        # A workshop at "00:00": the site's way of saying it has the date only (~300 of ~21,000, Nov 2026).
        time_known=has_time(item.get("startDate")) and start.time() != time(0),
        end_at=parse_iso(item.get("endDate"), timezone),
        venue_name=venue,
        address=clean_str((address or {}).get("streetAddress")),
        city=city,
        country=None if country in ("", "IL") else country,
        latitude=geo.get("latitude") if isinstance(geo.get("latitude"), (int, float)) else None,
        longitude=geo.get("longitude") if isinstance(geo.get("longitude"), (int, float)) else None,
        event_types=[category],
        images=images,
        currency=clean_str(offer.get("priceCurrency")) or "ILS",
        min_price=price,
        max_price=price,
        is_sold_out=str(offer.get("availability") or "").endswith("SoldOut"),
        extra={"purchase_url": clean_str(offer.get("url"))},
    )
