"""Tzkrti (tzkrti.com, تذكرتي) - the Arab sector's ticketing site: concerts, theatre, family shows and
stand-up in Nazareth, Haifa, Akka, Tarshiha, Shefa-Amr, Rahat, Jaffa and more.

Laravel app with a public JSON API: ``/api/v5/filter-events?page=N&city=all&country=il&category=events``
(12 events a page, ``hasMore``; ~6 pages, upcoming events only). ``country=il`` keeps Israel: the same
site also sells in Jordan and the Palestinian Authority. Each event has its name (Arabic), dates, its
category (English and Arabic), its place with address and coordinates, the lowest price and the artwork
(``images/...`` under ``/storage/``). The event's page is ``/en/checkout/<id>`` (the slug pages loop).
robots.txt disallows nothing.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from pydantic import Field

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, html_to_text, parse_iso, to_decimal, to_float, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_PRICE = re.compile(r"(\d+(?:\.\d+)?)")


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


class TzkrtiScraper(BaseScraper[TzkrtiSettings]):
    name = "tzkrti"
    settings_model = TzkrtiSettings

    def default_headers(self) -> dict[str, str]:
        return {"Accept": "application/json"}

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        for page in range(1, s.max_pages + 1):
            payload = await self.get_json(
                s.site_url + s.listing_path, params={"page": page, **s.listing_params}, expect=dict
            )
            listing = payload.get("events") or {}
            for item in listing.get("items") or []:
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
    """One API item -> event; None for a past, private, unlisted or non-event (course) item."""
    if item.get("is_past") or item.get("private") or item.get("unlisted"):
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
