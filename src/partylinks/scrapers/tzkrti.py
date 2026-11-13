"""Tzkrti (tzkrti.com, تذكرتي) - the Arab sector's ticketing site: concerts, theatre, family shows and
stand-up in Nazareth, Haifa, Akka, Tarshiha, Shefa-Amr, Rahat, Jaffa and more.

Laravel app with a public JSON API: ``/api/v5/filter-events?page=N&city=all&country=il&category=events``
(12 events a page, ``hasMore``; ~6 pages, upcoming events only). ``country=il`` keeps Israel: the same
site also sells in Jordan and the Palestinian Authority. Each event has its name (Arabic), dates, its
category (English and Arabic), its place with address and coordinates, the lowest price and the artwork
(``images/...`` under ``/storage/``). The event's page is ``/en/checkout/<id>`` (the slug pages loop).
robots.txt disallows nothing.

Unlisted (link-only) events are left out of the API. Ids are sequential and ``/en/checkout/<id>`` embeds
the full event JSON (``:event="..."``) for any id, so after the listing every id from ``probe_back`` below
the newest listed one to ``probe_ahead`` past it is read too; only published, on-sale Israeli events are kept.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import httpx
from pydantic import Field

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, html_to_text, parse_iso, to_decimal, to_float, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_PRICE = re.compile(r"(\d+(?:\.\d+)?)")
_EMBEDDED = re.compile(r'[\s:]event="(\{&quot;.*?\})"', re.DOTALL)


class TzkrtiSettings(SourceSettings):
    site_url: str = "https://tzkrti.com"
    listing_path: str = "/api/v5/filter-events"
    listing_params: dict[str, str] = Field(
        default_factory=lambda: {"city": "all", "country": "il", "category": "events"}
    )
    event_page_path: str = "/en/checkout/{id}"
    image_prefix: str = "/storage/"
    max_pages: int = Field(30, ge=1)
    # Categories that are not events (a course of lessons).
    skip_categories: list[str] = Field(default_factory=lambda: ["courses"])
    # Unlisted events: ids this far below / above the newest listed one are read one by one (0 and 0: off).
    probe_back: int = Field(800, ge=0)
    probe_ahead: int = Field(30, ge=0)
    probe_country: str = "IL"
    probe_sale_status: list[str] = Field(default_factory=lambda: ["on sale", "sold out", "happening now"])


class TzkrtiScraper(BaseScraper[TzkrtiSettings]):
    name = "tzkrti"
    settings_model = TzkrtiSettings

    def default_headers(self) -> dict[str, str]:
        return {"Accept": "application/json"}

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        listed: dict[int, dict[str, Any]] = {}  # event id -> listing item
        async for event in self._listing(listed):
            yield event
        s = self.settings
        if not listed or not (s.probe_back or s.probe_ahead):
            return
        # A checkout page's category is null: its category_id is looked up among the listed items.
        categories = {i.get("category_id"): i.get("category") for i in listed.values() if i.get("category")}
        newest = max(listed)
        gaps = [i for i in range(max(1, newest - s.probe_back), newest + s.probe_ahead + 1) if i not in listed]
        async for event in self.stream_isolated(gaps, lambda i: self._probe_unlisted(i, categories), label=str):
            yield event

    async def _probe_unlisted(self, event_id: int, categories: dict[Any, dict[str, Any]]) -> ScrapedEvent | None:
        """An id missing from the listing: None unless it is a published, on-sale event in Israel."""
        s = self.settings
        try:
            page = await self.get_text(s.site_url + s.event_page_path.format(id=event_id))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise
        item = parse_embedded(page)
        if not item or not probe_wanted(item, s):
            return None
        item["category"] = item.get("category") or categories.get(item.get("category_id")) or {}
        event = parse_event(item, s, self.timezone)
        if event and is_current(event.start_at, event.end_at, RetentionSettings(), self.timezone):
            return event
        return None

    async def _listing(self, listed: dict[int, dict[str, Any]]) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        for page in range(1, s.max_pages + 1):
            payload = await self.get_json(
                s.site_url + s.listing_path, params={"page": page, **s.listing_params}, expect=dict
            )
            listing = payload.get("events") or {}
            for item in listing.get("items") or []:
                if isinstance(item.get("id"), int):
                    listed[item["id"]] = item
                try:
                    event = parse_event(item, s, self.timezone)
                except Exception as exc:  # noqa: BLE001 - one odd event never drops the others
                    self.record_failure(f"event {item.get('id')}", exc)
                    continue
                if event and is_current(event.start_at, event.end_at, RetentionSettings(), self.timezone):
                    yield event
            if not payload.get("hasMore"):
                return
        self.log.warning("listing truncated at max_pages=%d", s.max_pages)


def parse_embedded(page: str) -> dict[str, Any] | None:
    """The event JSON a checkout page carries in its ``:event`` attribute."""
    match = _EMBEDDED.search(page)
    if not match:
        return None
    try:
        data = json.loads(html.unescape(match.group(1)))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def probe_wanted(item: dict[str, Any], s: TzkrtiSettings) -> bool:
    """Published, not cancelled, selling, in the probed country (drafts and "On Hold" are not live)."""
    return (
        bool(item.get("published"))
        and not item.get("cancelled")
        and str(item.get("country_iso") or "").upper() == s.probe_country.upper()
        and str(item.get("sale_status") or "").lower() in s.probe_sale_status
    )


def _text(value: Any) -> str | None:
    """A {"ar": ..., "en": ...} field (or a plain string): the Arabic, else the English."""
    if isinstance(value, dict):
        return clean_str(value.get("ar")) or clean_str(value.get("en"))
    return clean_str(value)


def _local(value: datetime | None, timezone: str) -> datetime | None:
    return value.astimezone(zone(timezone)) if value else None


def _english(value: Any) -> str | None:
    return clean_str(value.get("en")) if isinstance(value, dict) else clean_str(value)


def parse_event(item: dict[str, Any], s: TzkrtiSettings, timezone: str) -> ScrapedEvent | None:
    """One API item -> event; None for a past or non-event (course) item. Private / unlisted are kept."""
    if item.get("is_past"):
        return None
    category = item.get("category") or {}
    names = category.get("name") or {}
    label = (clean_str(names.get("en")) or clean_str(category.get("label")) or "").lower()
    if label in s.skip_categories:
        return None
    # from_date / to_date are UTC ("2026-11-21 18:00" is the 20:00 show its from_time_only names).
    start_at = _local(parse_iso(item.get("from_date"), "UTC"), timezone)
    title = clean_str(item.get("label")) or _text(item.get("name"))
    if start_at is None or not title:
        return None
    end_at = _local(parse_iso(item.get("to_date"), "UTC"), timezone)
    place = item.get("place_obj") or {}
    city = place.get("city") or {}
    lat, lng = to_float(place.get("latitude")), to_float(place.get("longitude"))
    price = _PRICE.search(str(item.get("lowest_price") or "").replace(",", ""))
    image = clean_str(item.get("image"))
    url = s.site_url + s.event_page_path.format(id=item["id"])
    return ScrapedEvent(
        source=TzkrtiScraper.name,
        source_event_id=str(item["id"]),
        url=url,
        title=title,
        description=html_to_text(_text(item.get("content")) or ""),
        start_at=start_at,
        end_at=end_at if end_at and end_at > start_at else None,
        venue_name=_text(place.get("name")) or _text(item.get("place")),
        address=clean_str(place.get("address")),
        city=_english(city.get("name")) or _english(item.get("city_name")),  # the city list maps English names
        latitude=lat if lat is not None and lng is not None else None,
        longitude=lng if lat is not None and lng is not None else None,
        source_genres=[g for g in (label, clean_str(names.get("ar"))) if g],
        images=[s.site_url + s.image_prefix + image] if image else [],
        min_price=to_decimal(price.group(1)) if price else None,
        is_sold_out=str(item.get("sale_status") or "").lower() == "sold out",
        extra={"purchase_url": url},
    )
