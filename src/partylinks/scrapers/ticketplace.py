"""Ticket Place (ticketplace.co.il) - resale tickets to concerts and festivals abroad.

WordPress/WooCommerce. Listing: the WP REST products endpoint (``/wp-json/wp/v2/product``,
the Store API answers 500 here) filtered to the shows and festivals categories, 100 a page,
total in ``X-WP-Total``. Details: each product page's heading gives the local date and time,
the location line "venue, city, country" (Hebrew country name), the seat categories'
prices (euros) and the artwork. One ``ScrapedEvent`` per product (= one performance);
products of past shows stay listed and are dropped by the service. ~4 + ~300 requests.
robots.txt is absent (404).
"""

from __future__ import annotations

import html as htmllib
import re
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from pydantic import Field

from partylinks.models import ScrapedEvent
from partylinks.parsing import ParseError, clean_str, to_decimal, to_int, zone
from partylinks.scrapers.abroad import country_code, local_zone
from partylinks.scrapers.base import BaseScraper, SourceSettings

PAGE_SIZE = 100
MONTHS = {
    m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)
}
CURRENCY_SIGNS = {"€": "EUR", "£": "GBP", "$": "USD", "₪": "ILS"}

_HEADING = re.compile(r'<h1 class="s-heading__title">(.*?)</h1>', re.DOTALL)
_TITLE = re.compile(r"</span>\s*<span>(.*?)</span>", re.DOTALL)
_DATE = re.compile(r"(\d{1,2})\s*ב?([A-Za-z]{3})[A-Za-z]*\s+(\d{4})")
_TIME = re.compile(r"(\d{1,2}):(\d{2})")
_LOCATION = re.compile(r'class="event-location[^"]*">.*?<span>(.*?)</span>', re.DOTALL)
_PRICE = re.compile(r'woocommerce-Price-amount amount"><bdi[^>]*>(.*?)</bdi>', re.DOTALL)
_IMAGE = re.compile(r'id="pop"><img[^>]*?\ssrc="([^"]+)"')
_POST_ID = re.compile(r"postid-(\d+)")


class TicketPlaceSettings(SourceSettings):
    site_url: str
    products_path: str  # WP REST products endpoint
    category_ids: list[int]  # shows, festivals
    festival_category_id: int
    max_pages: int = Field(10, ge=1)


class TicketPlaceScraper(BaseScraper[TicketPlaceSettings]):
    name = "ticketplace"
    settings_model = TicketPlaceSettings
    abroad_only = True

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        products = await self.paginate(self._listing_page, PAGE_SIZE, s.max_pages)
        festivals = {
            p["link"]: s.festival_category_id in (p.get("product_cat") or [])
            for p in products
            if isinstance(p, dict) and p.get("link")
        }
        self.log.info("listed %d products", len(festivals))

        async def fetch(url: str) -> ScrapedEvent | None:
            return parse_product(await self.get_text(url), url, self.timezone, festival=festivals[url])

        async for event in self.stream_isolated(festivals, fetch, label=lambda u: u):
            yield event

    async def _listing_page(self, index: int) -> tuple[list[Any], int | None]:
        s = self.settings
        params = {
            "per_page": PAGE_SIZE,
            "page": index + 1,
            "product_cat": ",".join(map(str, s.category_ids)),
            "_fields": "id,link,product_cat",
        }
        response = await self.request("GET", s.site_url + s.products_path, params=params)
        try:
            items = response.json()
        except ValueError as exc:
            raise ParseError(f"{response.request.url}: expected JSON") from exc
        if not isinstance(items, list):
            raise ParseError(f"{response.request.url}: expected a list")
        return items, to_int(response.headers.get("X-WP-Total"))


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def parse_product(page_html: str, url: str, timezone: str, *, festival: bool = False) -> ScrapedEvent | None:
    """The product's performance; None for a page without a dated heading (not an event)."""
    heading = _HEADING.search(page_html)
    date = _DATE.search(heading.group(1)) if heading else None
    if not heading or not date or date.group(2).lower() not in MONTHS:
        return None
    title_match = _TITLE.search(heading.group(1))
    title = clean_str(_text(title_match.group(1))) if title_match else None
    if not title:
        raise ParseError(f"{url}: heading without a title")
    location = _LOCATION.search(page_html)
    parts = [p.strip() for p in _text(location.group(1)).split(",") if p.strip()] if location else []
    country = country_code(parts[-1]) if parts else country_code(None)
    city = parts[-2] if len(parts) >= 2 else None
    venue = parts[0] if len(parts) >= 3 else None
    time = _TIME.search(heading.group(1)[date.end() :])
    start_at = datetime(
        int(date.group(3)),
        MONTHS[date.group(2).lower()],
        int(date.group(1)),
        int(time.group(1)) if time else 0,
        int(time.group(2)) if time else 0,
        tzinfo=zone(local_zone(country, timezone, city) if time else timezone),  # a date alone: Israel's day
    )
    amounts = [_text(p) for p in _PRICE.findall(page_html)]
    prices = [d for a in amounts if (d := to_decimal(re.sub(r"[^\d.]", "", a)))]
    currency = next((c for sign, c in CURRENCY_SIGNS.items() if any(sign in a for a in amounts)), "EUR")
    # "<artist> ב<city>" (the city in Hebrew even when the location line has it in English).
    artist = title.rpartition(" ב")[0].strip() or title
    image = _IMAGE.search(page_html)
    post_id = _POST_ID.search(page_html)
    return ScrapedEvent(
        source=TicketPlaceScraper.name,
        source_event_id=post_id.group(1) if post_id else url.rstrip("/").rsplit("/", 1)[-1],
        url=url,
        title=title,
        start_at=start_at,
        time_known=time is not None,
        venue_name=venue,
        city=city,
        country=country,
        source_genres=["פסטיבל"] if festival else [],
        artists=[artist] if artist and not festival else [],
        images=[image.group(1)] if image else [],
        currency=currency,
        min_price=min(prices) if prices else None,
        max_price=max(prices) if prices else None,
        is_sold_out=bool(prices) and "בחר כמות" not in page_html,
    )
