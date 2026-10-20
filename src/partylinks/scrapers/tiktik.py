"""TikTik Online (tiktik-online.co.il) - tickets and packages for concerts and festivals abroad.

WooCommerce shop. Listing: the public Store API (``/wp-json/wc/store/v1/products``) filtered
to the music category, 100 products a page, total in ``X-WP-Total``. Its prices are labelled
ILS but are euros, and it has no date or venue. Details: each product page carries a
schema.org ``MusicEvent`` (start, venue, city, country, performer, low/high price in EUR).
One ``ScrapedEvent`` per product; ``source_event_id`` is the product id. Every event gets
``country`` (ISO code), which keeps it out of the Israeli feed and in the abroad section.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from pydantic import Field

from partylinks.models import ScrapedEvent
from partylinks.parsing import (
    ParseError,
    clean_str,
    html_to_text,
    jsonld_nodes,
    parse_iso,
    schema_types,
    to_decimal,
    to_int,
)
from partylinks.scrapers.base import BaseScraper, SourceSettings

PAGE_SIZE = 100


class TiktikSettings(SourceSettings):
    site_url: str
    products_path: str  # Store API products endpoint
    category_id: int  # WooCommerce product category (music)
    max_pages: int = Field(40, ge=1)


class TiktikScraper(BaseScraper[TiktikSettings]):
    name = "tiktik"
    settings_model = TiktikSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        products = await self.paginate(self._listing_page, PAGE_SIZE, s.max_pages)
        urls = list(dict.fromkeys(p["permalink"] for p in products if isinstance(p, dict) and p.get("permalink")))
        self.log.info("listed %d products", len(urls))
        async for event in self.stream_isolated(urls, self._fetch_product, label=lambda u: u):
            yield event

    async def _listing_page(self, index: int) -> tuple[list[Any], int | None]:
        s = self.settings
        params = {"category": s.category_id, "per_page": PAGE_SIZE, "page": index + 1}
        response = await self.request("GET", s.site_url + s.products_path, params=params)
        try:
            items = response.json()
        except ValueError as exc:
            raise ParseError(f"{response.request.url}: expected JSON") from exc
        if not isinstance(items, list):
            raise ParseError(f"{response.request.url}: expected a list")
        return items, to_int(response.headers.get("X-WP-Total"))

    async def _fetch_product(self, url: str) -> ScrapedEvent | None:
        return parse_product(await self.get_text(url), url, self.timezone)


def parse_product(page_html: str, url: str, timezone: str) -> ScrapedEvent | None:
    """The product's ``MusicEvent``; None for a product without one (gift card, merch)."""
    nodes = jsonld_nodes(page_html)
    event = next((n for n in nodes if "musicevent" in schema_types(n)), None)
    if event is None:
        return None
    title = clean_str(event.get("name"))
    start_at = parse_iso(event.get("startDate"), timezone)  # a date alone: the venue's time is unknown
    if not title or not start_at:
        raise ParseError(f"{url}: MusicEvent without name or start")
    product = next((n for n in nodes if "product" in schema_types(n) and str(n.get("sku", "")).startswith("TIKTIK-")), {})
    product_id = str(product.get("sku", "")).removeprefix("TIKTIK-") or None
    location = event.get("location") if isinstance(event.get("location"), dict) else {}
    address = location.get("address") if isinstance(location.get("address"), dict) else {}
    offers = event.get("offers") if isinstance(event.get("offers"), dict) else {}
    performer = event.get("performer") if isinstance(event.get("performer"), dict) else {}
    country = (clean_str(address.get("addressCountry")) or "").upper()
    low, high = to_decimal(offers.get("lowPrice")), to_decimal(offers.get("highPrice"))
    image = clean_str(event.get("image"))
    artist = clean_str(performer.get("name"))
    return ScrapedEvent(
        source=TiktikScraper.name,
        source_event_id=product_id or url.rstrip("/").rsplit("/", 1)[-1],
        url=url,
        title=title,
        description=html_to_text(event.get("description")),
        start_at=start_at,
        end_at=parse_iso(event.get("endDate"), timezone) if "T" in str(event.get("endDate", "")) else None,
        venue_name=clean_str(location.get("name")),
        city=clean_str(address.get("addressLocality")),
        country=country if country and country != "IL" else None,
        artists=[artist] if artist else [],
        images=[image] if image else [],
        currency=clean_str(offers.get("priceCurrency")) or "EUR",
        min_price=low or None,
        max_price=high or low or None,
        is_sold_out="instock" not in str(offers.get("availability", "")).lower(),
        extra={"package": "חבילה" in str(product.get("name", ""))},
    )
