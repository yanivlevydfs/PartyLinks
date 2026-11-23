"""Ticket Place (ticketplace.co.il) - resale tickets to concerts, festivals and sport abroad.

WordPress/WooCommerce. Listing: the WP REST products endpoint (``/wp-json/wp/v2/product``,
the Store API answers 500 here), the whole catalogue (shows, festivals, sport: ~1,000), 100 a
page, total in ``X-WP-Total``; the product categories endpoint names each product's sections
(a match's league / team / competition become its declared genres). Details: each product page's heading gives the local date and time,
the location line "venue, city, country" (Hebrew country name), the seat categories'
prices (euros) and the artwork. One ``ScrapedEvent`` per product (= one performance);
products of past shows stay listed and are dropped by the service. ~12 + ~1,000 requests.
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
    categories_path: str  # WP REST product categories endpoint
    festival_category_id: int
    sport_category_id: int
    max_pages: int = Field(100, ge=1)  # a safety net only (10 pages on 23.11.2026)
    page_size: int = Field(100, ge=1, le=100)  # products per listing request (WordPress allows 100 at most)


class TicketPlaceScraper(BaseScraper[TicketPlaceSettings]):
    name = "ticketplace"
    settings_model = TicketPlaceSettings
    abroad_only = True

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        categories = await self._categories()
        products = await self.paginate(self._listing_page, s.page_size, s.max_pages)
        cats = {
            p["link"]: [c for c in p.get("product_cat") or [] if isinstance(c, int)]
            for p in products
            if isinstance(p, dict) and p.get("link")
        }
        self.log.debug("listed %d products", len(cats))

        def root(cat: int) -> int:
            seen: set[int] = set()
            while cat in categories and categories[cat][1] and cat not in seen:
                seen.add(cat)
                cat = categories[cat][1]
            return cat

        async def fetch(url: str) -> ScrapedEvent | None:
            roots = {root(c) for c in cats[url]}
            sport = None
            if s.sport_category_id in roots:  # the match's sections: "ספורט", "ליגה אנגלית", "ווימבלדון"...
                names = [categories[c][0] for c in cats[url] if c in categories and root(c) == s.sport_category_id]
                sport = list(dict.fromkeys([categories.get(s.sport_category_id, ("ספורט", 0))[0], *names]))
            festival = s.festival_category_id in roots
            return parse_product(await self.get_text(url), url, self.timezone, festival=festival, sport=sport)

        async for event in self.stream_isolated(cats, fetch, label=lambda u: u):
            yield event

    async def _listing_page(self, index: int) -> tuple[list[Any], int | None]:
        s = self.settings
        params = {
            "per_page": s.page_size,
            "page": index + 1,
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

    async def _categories(self) -> dict[int, tuple[str, int]]:
        """Every product category: id -> (name, parent id)."""
        s = self.settings

        async def page(index: int) -> tuple[list[Any], int | None]:
            params = {"per_page": 100, "page": index + 1, "_fields": "id,name,parent"}
            response = await self.request("GET", s.site_url + s.categories_path, params=params)
            try:
                items = response.json()
            except ValueError as exc:
                raise ParseError(f"{response.request.url}: expected JSON") from exc
            if not isinstance(items, list):
                raise ParseError(f"{response.request.url}: expected a list")
            return items, to_int(response.headers.get("X-WP-Total"))

        rows = await self.paginate(page, 100, s.max_pages)
        return {
            r["id"]: (clean_str(htmllib.unescape(str(r.get("name") or ""))) or "", to_int(r.get("parent")) or 0)
            for r in rows
            if isinstance(r, dict) and isinstance(r.get("id"), int)
        }


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def parse_product(
    page_html: str, url: str, timezone: str, *, festival: bool = False, sport: list[str] | None = None
) -> ScrapedEvent | None:
    """The product's performance; None for a page without a dated heading (not an event).

    ``sport``: a match's section names (its declared genres); a match names no performer.
    """
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
        source_genres=sport if sport else ["פסטיבל"] if festival else [],
        artists=[artist] if artist and not festival and not sport else [],
        images=[image.group(1)] if image else [],
        currency=currency,
        min_price=min(prices) if prices else None,
        max_price=max(prices) if prices else None,
        is_sold_out=bool(prices) and "בחר כמות" not in page_html,
        extra={"resale": True},  # second-hand tickets (shown apart on the event page)
    )
