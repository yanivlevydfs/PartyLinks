"""Bar Giyora (bar-giyora.co.il) - live-show bar on Ibn Gabirol, Tel Aviv: music, stand-up, lectures.

A WooCommerce shop, one product per show. Listing: ``/tickets/`` and ``/tickets/page/N/`` (9 cards a
page, ``rel="next"`` until the last) give title, link, date, door time, artwork and the show type
("מוזיקה", "סטנדאפ", "הרצאה"). The Store API (``/wp-json/wc/store/v1/products?include=...``) adds price,
stock and description for those products in one call; it has no date, so it never lists shows alone
(past products stay in stock there). Where its description is empty, the product page's is read.
One venue, so its place comes from the config.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from pydantic import Field
from selectolax.lexbor import LexborHTMLParser

from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import clean_str, html_to_text, to_decimal, zone
from partylinks.scrapers.base import BaseScraper, SourceSettings

_DATE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")
_TIME = re.compile(r"(\d{1,2}):(\d{2})")
_IMAGE = re.compile(r"url\(([^)]+)\)")


class BarGiyoraSettings(SourceSettings):
    site_url: str
    listing_path: str  # first listing page; later ones are <listing_path>page/N/
    products_path: str  # Store API products endpoint
    max_pages: int = Field(30, ge=1)
    venue_name: str
    address: str
    city: str
    latitude: float | None = None
    longitude: float | None = None


class BarGiyoraScraper(BaseScraper[BarGiyoraSettings]):
    name = "bargiyora"
    settings_model = BarGiyoraSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        cards: dict[str, dict[str, str]] = {}
        for index in range(s.max_pages):
            path = s.listing_path + (f"page/{index + 1}/" if index else "")
            fetch = self.get_text(s.site_url + path)  # the first page must load; a later one may fail alone
            page = await (self.optional(f"listing page {index + 1}", fetch) if index else fetch)
            if page is None:
                break
            for card in parse_listing(page):
                cards.setdefault(card["id"], card)
            if 'rel="next"' not in page:
                break
        else:
            self.log.warning("listing truncated at max_pages=%d", s.max_pages)
        self.log.info("listed %d events", len(cards))

        products: dict[str, dict[str, Any]] = {}
        ids = list(cards)
        for start in range(0, len(ids), 100):
            batch = await self.optional(
                "store api",
                self.get_json(
                    s.site_url + s.products_path,
                    params={"include": ",".join(ids[start : start + 100]), "per_page": 100},
                    expect=list,
                ),
            )
            products.update({str(p.get("id")): p for p in batch or [] if isinstance(p, dict)})

        for card in cards.values():
            try:
                event = parse_event(card, products.get(card["id"]), s, self.timezone)
            except Exception as exc:  # noqa: BLE001 - one odd card never drops the others
                self.record_failure(f"event {card['url']}", exc)
                continue
            if event is None:
                continue
            if not event.description:  # the Store API leaves some empty; the product page has them
                page = await self.optional(f"page {card['url']}", self.get_text(card["url"]))
                event.description = parse_description(page) if page else None
            yield event


def parse_description(page_html: str) -> str | None:
    """Product page -> the show's text (its ``.proud_present`` block)."""
    node = LexborHTMLParser(page_html).css_first(".proud_present")
    return html_to_text(node.html) if node else None


def parse_listing(page_html: str) -> list[dict[str, str]]:
    """Listing page -> one dict per card: id, url, title, date, time, image, kind."""
    cards = []
    for node in LexborHTMLParser(page_html).css("div.product[data-post]"):
        link = node.css_first(".performance-con h5 a")
        url = link.attributes.get("href") if link else None
        if link is None or not url:
            continue
        image_node = node.css_first(".performance-img")
        image = _IMAGE.search(image_node.attributes.get("style") or "") if image_node else None
        text = {
            cls: clean_str(n.text()) or "" for cls in ("date", "time", "tag-text") if (n := node.css_first(f".{cls}"))
        }
        cards.append(
            {
                "id": node.attributes.get("data-post") or "",
                "url": url,
                "title": clean_str(link.text()) or "",
                "date": text.get("date", ""),
                "time": text.get("time", ""),
                "kind": text.get("tag-text", ""),
                "image": image.group(1).strip("'\" ") if image else "",
            }
        )
    return cards


def parse_event(
    card: dict[str, str], product: dict[str, Any] | None, s: BarGiyoraSettings, timezone: str
) -> ScrapedEvent | None:
    """A listing card (+ its Store API product, if any) -> event; None without a date or title."""
    day = _DATE.search(card["date"])
    if not day or not card["title"]:
        return None
    clock = _TIME.search(card["time"])
    hour, minute = clock.groups() if clock else ("0", "0")
    start_at = datetime(int(day[3]), int(day[2]), int(day[1]), int(hour), int(minute), tzinfo=zone(timezone))

    product = product or {}
    prices = product.get("prices") or {}
    price = to_decimal(prices.get("price"))
    images = [card["image"], *((i or {}).get("src") for i in product.get("images") or [])]
    return ScrapedEvent(
        source=BarGiyoraScraper.name,
        source_event_id=card["id"],
        url=card["url"],
        title=card["title"],
        description=html_to_text(product.get("description")),
        start_at=start_at,
        venue_name=s.venue_name,
        address=s.address,
        city=s.city,
        latitude=s.latitude,
        longitude=s.longitude,
        source_genres=[card["kind"]] if card["kind"] else [],
        images=list(dict.fromkeys(i for i in images if isinstance(i, str) and i.startswith("http"))),
        min_price=price,
        max_price=price,
        is_sold_out=product.get("is_in_stock") is False,
        tickets=[
            TicketTier(
                name="כרטיס רגיל",
                price=price,
                original_price=to_decimal(prices.get("regular_price")),
                is_free=price == 0,
            )
        ]
        if price is not None
        else [],
        extra={"doors_open": clock.group(0) if clock else None, "purchase_url": card["url"]},
    )
