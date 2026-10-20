"""Hulyo (hulyo.co.il) - last-minute show deals.

Listing: one static JSON feed (``/dynamic/client/shows.json``) holds every show deal
nationwide under ``products`` and ``promotedProducts`` (id -> deal). Each deal is
one performance with date, time, venue, address, area, category and the deal
price, so no detail requests are needed. Deals are kept for the configured areas.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import Field

from partylinks.models import ScrapedEvent, TicketTier, TierStatus
from partylinks.parsing import ParseError, clean_str, html_to_text, to_decimal
from partylinks.scrapers.base import BaseScraper, SourceSettings


class HulyoSettings(SourceSettings):
    site_url: str
    feed_path: str
    event_page_path: str
    # Empty list = every area.
    areas: list[str] = Field(default_factory=list)


class HulyoScraper(BaseScraper[HulyoSettings]):
    name = "hulyo"
    settings_model = HulyoSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        feed = await self.get_json(s.site_url + s.feed_path, expect=dict)
        deals = list_deals(feed, s.areas)
        self.log.info("listed %d events", len(deals))
        for deal in deals:
            try:
                yield parse_event(deal, s, self.timezone)
            except Exception as exc:  # noqa: BLE001 - one bad deal never drops the feed
                self.record_failure(str(deal.get("id")), exc)


def list_deals(feed: dict[str, Any], areas: list[str]) -> list[dict[str, Any]]:
    deals: dict[str, dict[str, Any]] = {}
    for key in ("products", "promotedProducts"):
        group = feed.get(key)
        for deal in group.values() if isinstance(group, dict) else []:
            if isinstance(deal, dict) and deal.get("id") and (not areas or deal.get("areaName") in areas):
                deals[str(deal["id"])] = deal
    return list(deals.values())


def parse_event(deal: dict[str, Any], s: HulyoSettings, timezone: str) -> ScrapedEvent:
    deal_id = clean_str(deal.get("id"))
    if not deal_id:
        raise ParseError("id missing")
    price = to_decimal(deal.get("sellingPrice"))
    sold_out = bool(deal.get("soldOut"))
    category = clean_str(deal.get("category"))
    image = clean_str(deal.get("imageUrl"))
    return ScrapedEvent(
        source=HulyoScraper.name,
        source_event_id=deal_id,
        url=s.site_url + s.event_page_path.format(id=deal_id),
        title=clean_str(deal.get("name")) or "",
        description=html_to_text(deal.get("description")),
        start_at=_parse_start(deal.get("date"), deal.get("time"), timezone),
        venue_name=clean_str(deal.get("placeName")),
        address=clean_str(deal.get("address")),
        event_types=[category] if category else [],
        organizer=clean_str(deal.get("supplierId")),
        images=[image] if image else [],
        currency=clean_str(deal.get("currencyIso")) or "ILS",
        min_price=price,
        max_price=price,
        is_sold_out=sold_out,
        tickets=(
            [TicketTier(name="כרטיס", price=price, is_free=price == 0, status=TierStatus.SOLD_OUT if sold_out else TierStatus.ON_SALE)]
            if price is not None
            else []
        ),
        extra={
            "area": clean_str(deal.get("areaName")),
            "remark": html_to_text(deal.get("supplierRemark")),
            "youtube": clean_str(deal.get("youtubeUrl")),
        },
    )


def _parse_start(day: Any, time: Any, timezone: str) -> datetime | None:
    """Local ``yyyy-mm-dd`` + ``HH:MM``."""
    day_s, time_s = clean_str(day), clean_str(time) or "00:00"
    if not day_s:
        return None
    return datetime.strptime(f"{day_s} {time_s}", "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo(timezone))
