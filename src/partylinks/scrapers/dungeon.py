"""The Dungeon (dungeon.co.il, "הדאנג'ן") - Tel Aviv's fetish / BDSM club (HaArba'a 10), parties on Thursday
and Friday nights (18+, a dress code).

The site is WordPress + WooCommerce: each party is a product, every product is in the WooCommerce Store API
(``/wp-json/wc/store/v1/products``, 100 a page, read to the last page; 33 on 24.11.2026, the same 33 as the
product sitemap). Products in the category "יחצנים" (promoters) are the promoters' own ticket links for the
same nights (a "_special-discount" / "_single-ticket" copy, a test product): left out, only the public
products ("ציבורי") are events. The night is the "DD.MM" in the product name; the year is the coming one
(the artwork file says "DD-MM-YY" too). The description gives the hour ("פתיחה בשעה 23:30", "פתיחה בחצות")
once the line-up is out; a product still saying "פרטים בקרוב" has a date only. The price is the pre-sale
price (the box office costs more: the regular price). One venue: its pin is in config.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from html import unescape
from typing import Any

from pydantic import Field

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import clean_str, html_to_text, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_NIGHT = re.compile(r"(?<![\d.])(\d{1,2})\.(\d{1,2})(?![\d.])")
_HOUR = re.compile(r"פתיחה\s+(?:בשעה\s+)?(\d{1,2}):(\d{2})")
_MIDNIGHT = re.compile(r"פתיחה\s+בחצות")


class DungeonSettings(SourceSettings):
    site_url: str = "https://dungeon.co.il"
    products_path: str = "/wp-json/wc/store/v1/products?per_page={per_page}&page={page}"
    per_page: int = 100  # the Store API's maximum
    max_pages: int = 50  # a runaway guard only: the shop holds a few dozen products
    # Product categories that are no public event (promoters' ticket links).
    skip_categories: list[str] = Field(default_factory=lambda: ["יחצנים"])
    # Every night of the club is a fetish party (the site has no genres): its own label.
    club_genres: list[str] = Field(default_factory=lambda: ["kink"])
    venue_name: str = "הדאנג'ן"
    address: str = "הארבעה 10"
    city: str = "תל אביב"
    latitude: float | None = None
    longitude: float | None = None


def night_of(name: str, today: date) -> date | None:
    """The party's night from its name ("Fetish Thursdays 3.12"): the coming DD.MM (a date a month or more
    behind today is next year's)."""
    found = _NIGHT.findall(name)
    if not found:
        return None
    day, month = (int(x) for x in found[-1])
    for year in (today.year, today.year + 1):
        try:
            night = date(year, month, day)
        except ValueError:
            return None
        if night >= today - timedelta(days=31):
            return night
    return None


def opening(text: str) -> time | None:
    """The hour the doors open, from the description; "פתיחה בחצות" (at midnight) is the last minute of the
    night named (a 00:00 start would read as a date without an hour, on the next day)."""
    if m := _HOUR.search(text):
        hour, minute = int(m.group(1)), int(m.group(2))
        return time(23, 59) if (hour, minute) in {(0, 0), (24, 0)} else time(hour % 24, minute)
    return time(23, 59) if _MIDNIGHT.search(text) else None


def _money(prices: dict[str, Any], key: str) -> Decimal | None:
    raw = str(prices.get(key) or "").strip()
    if not raw.isdigit():
        return None
    return Decimal(raw) / (10 ** int(prices.get("currency_minor_unit") or 0))


def parse_product(p: dict[str, Any], s: DungeonSettings, timezone: str, today: date) -> ScrapedEvent | None:
    """One public product -> its party; None for a promoters' product or one without a night."""
    categories = {
        clean_str(unescape(str(c.get("name") or ""))) for c in p.get("categories") or [] if isinstance(c, dict)
    }
    if categories & set(s.skip_categories) or not p.get("id"):
        return None
    name = clean_str(unescape(str(p.get("name") or "")))
    night = night_of(name or "", today)
    if not name or night is None:
        return None
    description = html_to_text(str(p.get("description") or ""))
    hour = opening(description or "")
    start = datetime.combine(night, hour or time(0, 0), zone(timezone))
    prices = p.get("prices") if isinstance(p.get("prices"), dict) else {}
    price = _money(prices, "price")
    regular = _money(prices, "regular_price")
    currency = clean_str(prices.get("currency_code")) or "ILS"
    tiers = [TicketTier(name="מכירה מוקדמת", price=price, currency=currency)] if price is not None else []
    if regular is not None and price is not None and regular > price:
        tiers.append(TicketTier(name="מחיר מלא", price=regular, currency=currency))
    images = [
        str(i["src"]) for i in p.get("images") or [] if isinstance(i, dict) and str(i.get("src", "")).startswith("http")
    ]
    return ScrapedEvent(
        source=DungeonScraper.name,
        source_event_id=str(p["id"]),
        url=str(p.get("permalink") or f"{s.site_url}/product/{p.get('slug')}/"),
        title=name,
        description=description,
        start_at=start,
        time_known=hour is not None,
        venue_name=s.venue_name,
        address=s.address,
        city=s.city,
        latitude=s.latitude,
        longitude=s.longitude,
        images=images[:1],
        tickets=tiers,
        currency=currency,
        min_price=price,
        max_price=max((t.price for t in tiers if t.price is not None), default=None),
        is_sold_out=p.get("is_in_stock") is False,
        source_genres=list(s.club_genres),
        min_age=18,
        organizer="The Dungeon",
    )


class DungeonScraper(BaseScraper[DungeonSettings]):
    name = "dungeon"
    settings_model = DungeonSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        products: list[Any] = []
        for page in range(1, s.max_pages + 1):  # every page, to the last (a short one)
            batch = await self.get_json(
                s.site_url + s.products_path.format(per_page=s.per_page, page=page), expect=list
            )
            products.extend(batch)
            if len(batch) < s.per_page:
                break
        today = datetime.now(zone(self.timezone)).date()
        retention = RetentionSettings()
        events = []
        for product in products:
            try:
                event = parse_product(product, s, self.timezone, today)
            except Exception as exc:  # noqa: BLE001 - one odd product never drops the others
                self.record_failure(f"product {product.get('id') if isinstance(product, dict) else product!r}", exc)
                continue
            if event and is_current(
                event.start_at, event.end_at, retention, self.timezone, time_known=event.time_known
            ):
                events.append(event)
        for event in sorted(events, key=lambda e: e.start_at.timestamp() if e.start_at else float("inf")):
            yield event
