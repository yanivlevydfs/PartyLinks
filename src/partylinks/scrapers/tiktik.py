"""TikTik Online (tiktik-online.co.il) - tickets and packages for concerts, festivals and sport abroad.

WooCommerce shop. Listing: the public Store API (``/wp-json/wc/store/v1/products``) filtered
to each configured category (music, sport), 100 products a page in id order, total in ``X-WP-Total``. Its prices
are labelled ILS but are euros, and it has no date or venue. Details: each product page carries
a schema.org ``MusicEvent`` or, for a match, a ``SportsEvent`` (start, venue, city, country,
performer, low/high price in EUR); a match is tagged "ספורט".
About one page in six has no ``Product``/offers block: its id, price and stock then come from
the listing. One ``ScrapedEvent`` per product; ``source_event_id`` is the product id. Every
event gets ``country`` (ISO code; "ZZ" when the page names none, since TikTik sells only
abroad), which keeps it out of the Israeli feed and in the abroad section.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from pydantic import Field

from partylinks.enrichment import UNKNOWN_COUNTRY
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
    category_ids: list[int]  # WooCommerce product categories (music, sport)
    max_pages: int = Field(40, ge=1)


class TiktikScraper(BaseScraper[TiktikSettings]):
    name = "tiktik"
    settings_model = TiktikSettings
    abroad_only = True

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        by_url: dict[str, Any] = {}
        for category in s.category_ids:
            products = await self.paginate(lambda index: self._listing_page(category, index), PAGE_SIZE, s.max_pages)  # noqa: B023 - awaited here
            by_url.update((p["permalink"], p) for p in products if isinstance(p, dict) and p.get("permalink"))
        self.log.info("listed %d products", len(by_url))
        async for event in self.stream_isolated(by_url.values(), self._fetch_product, label=lambda p: p["permalink"]):
            yield event

    async def _listing_page(self, category: int, index: int) -> tuple[list[Any], int | None]:
        s = self.settings
        # The default order (by date) drops ~2% of products across pages; by id every one is listed.
        params = {"category": category, "per_page": PAGE_SIZE, "page": index + 1, "orderby": "id", "order": "asc"}
        response = await self.request("GET", s.site_url + s.products_path, params=params)
        try:
            items = response.json()
        except ValueError as exc:
            raise ParseError(f"{response.request.url}: expected JSON") from exc
        if not isinstance(items, list):
            raise ParseError(f"{response.request.url}: expected a list")
        return items, to_int(response.headers.get("X-WP-Total"))

    async def _fetch_product(self, listed: dict[str, Any]) -> ScrapedEvent | None:
        url = listed["permalink"]
        return parse_product(await self.get_text(url), url, self.timezone, listed)


def parse_product(page_html: str, url: str, timezone: str, listed: dict[str, Any] | None = None) -> ScrapedEvent | None:
    """The product's ``MusicEvent`` / ``SportsEvent``; None for a product without one (gift card, merch).

    ``listed`` is the product's Store API entry: the fallback for id, price and stock.
    """
    nodes = jsonld_nodes(page_html)
    event = next((n for n in nodes if schema_types(n) & {"musicevent", "sportsevent"}), None)
    if event is None:
        return None
    sport = "sportsevent" in schema_types(event)
    title = clean_str(event.get("name"))
    start_at = parse_iso(event.get("startDate"), timezone)  # a date alone: the venue's time is unknown
    if not title or not start_at:
        raise ParseError(f"{url}: event without name or start")
    product = next((n for n in nodes if "product" in schema_types(n) and str(n.get("sku", "")).startswith("TIKTIK-")), {})
    listed = listed or {}
    product_id = str(product.get("sku", "")).removeprefix("TIKTIK-") or str(listed.get("id") or "") or None
    location = event.get("location") if isinstance(event.get("location"), dict) else {}
    address = location.get("address") if isinstance(location.get("address"), dict) else {}
    offers = event.get("offers") if isinstance(event.get("offers"), dict) else {}
    performer = event.get("performer") if isinstance(event.get("performer"), dict) else {}
    country = (clean_str(address.get("addressCountry")) or "").upper()
    low, high = to_decimal(offers.get("lowPrice")), to_decimal(offers.get("highPrice"))
    in_stock = "instock" in str(offers.get("availability", "")).lower()
    if not offers and listed:  # the listing's price is in EUR too, despite its ILS label
        prices = listed.get("prices") if isinstance(listed.get("prices"), dict) else {}
        price = to_decimal(prices.get("price"))
        low = price / 10 ** (to_int(prices.get("currency_minor_unit")) or 0) if price else None
        in_stock = listed.get("is_in_stock") is True
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
        country=None if country == "IL" else country or UNKNOWN_COUNTRY,
        artists=[artist] if artist and not sport else [],
        source_genres=["ספורט"] if sport else [],
        images=[image] if image else [],
        currency=clean_str(offers.get("priceCurrency")) or "EUR",
        min_price=low or None,
        max_price=high or low or None,
        is_sold_out=not in_stock,
        extra={"package": "חבילה" in str(product.get("name", ""))},
    )
