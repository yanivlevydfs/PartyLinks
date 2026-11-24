"""IWANT (iwant.co.il) - a small Tel Aviv party box office (80s / new wave / dark eighties club nights,
grunge and Britpop day parties, trance).

Listing: the home page's own feed, ``/events_public.php``: one request returns every event the site
holds (~40, past ones too) with name, date, time, venue (sometimes "venue, street"), city, the site's
music genres (``genre``, comma separated: "שנות ה-80,גל חדש,דארק אייטיז"), image, lowest price,
status and sold-out flag. Like the home page, only ``status == "active"`` events are read; past
ones are dropped from the listing with ``is_current``. The owner allows reading it (robots.txt
lists the path under Disallow).

Tiers: each current event's page, ``/event.php?id=N``, lists the ticket types as ``.ticket-card``
elements (``data-name``, ``data-price``, the struck-out full price of an early round); the listing's
price is 0 for many events. One request per current event (~10). No end time, no coordinates: the
venue and city are geocoded. City "אחר" (other) names no city; the venue text carries it then.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from pydantic import Field
from selectolax.lexbor import LexborHTMLParser

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import ParseError, clean_str, read_clock, to_decimal, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_NOT_TEXT = re.compile(r"^[^\w\"']+")  # a leading pin emoji ("📍 Dream Stage")


class IwantSettings(SourceSettings):
    site_url: str = "https://iwant.co.il"
    listing_path: str = "/events_public.php"
    event_path: str = "/event.php"
    # Listing values that are no venue, city or genre.
    no_city: list[str] = Field(default_factory=lambda: ["אחר"])
    no_genre: list[str] = Field(default_factory=lambda: ["אחר"])


class IwantScraper(BaseScraper[IwantSettings]):
    name = "iwant"
    settings_model = IwantSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        data = await self.get_json(self.settings.site_url + self.settings.listing_path, expect=dict)
        retention = RetentionSettings()
        rows = data.get("events") or []
        slugs = unique_slugs(rows)
        current: list[ScrapedEvent] = []
        for row in rows:
            try:
                event = parse_event(row, self.settings, self.timezone, slugs)
            except Exception as exc:  # noqa: BLE001 - one bad row must not cost the others
                self.record_failure(f"event {row.get('id')}", exc)
                continue
            if event and is_current(
                event.start_at, event.end_at, retention, self.timezone, time_known=event.time_known
            ):
                current.append(event)
        # The feed holds past rows too, in no promised order: event pages soonest first (Nov 2026).
        current.sort(key=lambda e: e.start_at or datetime.max.replace(tzinfo=UTC))
        async for event in self.stream_isolated(current, self._with_tiers, lambda e: str(e.url)):
            yield event

    async def _with_tiers(self, event: ScrapedEvent) -> ScrapedEvent:
        """The event with its page's ticket tiers; without them when the page fails."""
        page_url = event_page(self.settings, event.source_event_id)
        page = await self.optional(f"tiers {event.source_event_id}", self.get_text(page_url))
        tiers = parse_tiers(page) if page else []
        prices = [t.price for t in tiers if t.price is not None and t.price > 0]
        if not prices:
            return event
        return event.model_copy(update={"tickets": tiers, "min_price": min(prices), "max_price": max(prices)})


def unique_slugs(rows: list[dict[str, Any]]) -> set[str]:
    """The feed's slugs (lowercased) that name one event only. The site reuses a slug for a later date
    ("grungenoon": ids 11 and 50) and /<slug> then opens the oldest (24.11.2026), so those keep /event.php."""
    names = [str(r.get("slug") or "").strip().lower() for r in rows]
    return {n for n in names if n and names.count(n) == 1}


def event_page(settings: IwantSettings, event_id: object) -> str:
    return f"{settings.site_url}{settings.event_path}?id={event_id}"


def parse_event(
    row: dict[str, Any], settings: IwantSettings, timezone: str, slugs: set[str] | None = None
) -> ScrapedEvent | None:
    """One listing row; None when the site does not show it (status other than active). Its link is the
    site's own short one, /<slug> (what the producers share and /go opens), when no other event has that slug."""
    if row.get("status") != "active":
        return None
    event_id, title = row.get("id"), clean_str(row.get("name"))
    try:
        day = datetime.strptime(str(row.get("date")), "%Y-%m-%d")
    except ValueError:
        day = None
    if not event_id or not title or day is None:
        raise ParseError("row without id, name or date")
    clock = read_clock(row.get("time"))
    start = datetime.combine(day.date(), clock or datetime.min.time(), zone(timezone))
    place = clean_str(_NOT_TEXT.sub("", str(row.get("venue") or "")))
    city = clean_str(row.get("city"))
    city = None if city in settings.no_city else city
    venue = clean_str(re.split(r"[,–]| - ", place)[0]) if place else None
    genres = [g.strip().lower() for g in str(row.get("genre") or "").split(",")]
    image = clean_str(row.get("image_url"))
    price = to_decimal(row.get("price"))
    slug = str(row.get("slug") or "").strip()
    url = f"{settings.site_url}/{slug}" if slugs and slug.lower() in slugs else event_page(settings, event_id)
    return ScrapedEvent(
        source=IwantScraper.name,
        source_event_id=str(event_id),
        url=url,
        title=title,
        description=clean_str(row.get("description")),
        start_at=start,
        time_known=clock is not None,
        venue_name=venue,
        address=", ".join(p for p in (place, city) if p) or None,
        city=city,
        source_genres=[g for g in genres if g and g not in settings.no_genre],
        images=[settings.site_url + image if image.startswith("/") else image] if image else [],
        min_price=price if price else None,
        max_price=price if price else None,
        is_sold_out=bool(int(row.get("sold_out") or 0) or int(row.get("manual_sold_out") or 0)),
        # The id link stays the merge key: 80sMusic turns IWANT's short links into it (rel=canonical).
        extra={"purchase_url": event_page(settings, event_id)},
    )


def parse_tiers(page_html: str) -> list[TicketTier]:
    """The event page's ``.ticket-card`` tiers: name, price, and the struck-out full price of an early round."""
    tiers = []
    for card in LexborHTMLParser(page_html).css(".tickets .ticket-card"):
        name = clean_str(card.attributes.get("data-name"))
        if not name:
            continue
        full = card.css_first(".full-price")
        tiers.append(
            TicketTier(
                name=name,
                price=to_decimal(card.attributes.get("data-price")),
                original_price=to_decimal(full.text().replace("₪", "").strip()) if full else None,
            )
        )
    return tiers
