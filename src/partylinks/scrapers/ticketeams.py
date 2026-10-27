"""Ticketeams (ticketeams.co.il) - tickets to football, basketball (NBA, Euroleague), tennis and Formula 1
abroad, and to concerts abroad.

WooCommerce + JetEngine. Every product is one match / show; the shop unpublishes it once played.
* ``/wp-json/wp/v2/product`` - every product (100 a page, total in ``X-WP-Total``): link, title and its
  terms' ids: ``product_cat``, ``stadiums`` / ``halls`` (venue), ``cities``, ``countrys``, ``arts`` (artist);
* ``/wp-json/wp/v2/<taxonomy>`` - those terms' names (Hebrew country names -> ISO code; categories with
  their parents, so "ליגה אנגלית" also reads "כדורגל");
* the product page - the date ("אפריל 16, 2027", a JetEngine field) and the venue's local kick-off time
  ("20:30") in its header, and a schema.org ``Product`` with low/high price in EUR and stock.
The Store API's prices are the same numbers labelled ILS, and it has no venue or date, so it is not read.
Every event gets ``country`` (ISO; "ZZ" if unknown), which keeps it in the abroad section.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import unquote

from pydantic import Field
from selectolax.lexbor import LexborHTMLParser

from partylinks.models import ScrapedEvent
from partylinks.parsing import (
    ParseError,
    clean_str,
    html_to_text,
    jsonld_nodes,
    schema_types,
    to_decimal,
    to_int,
    zone,
)
from partylinks.scrapers.abroad import country_code, local_zone
from partylinks.scrapers.base import BaseScraper, SourceSettings

PAGE_SIZE = 100
TAXONOMIES = ("product_cat", "stadiums", "halls", "cities", "countrys", "arts")
_MONTHS = {m: i for i, m in enumerate(
    ["ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני", "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר"], 1)}  # fmt: skip
_DATE = re.compile(rf"({'|'.join(_MONTHS)})\s+(\d{{1,2}}),\s*(\d{{4}})")
_TIME = re.compile(r"(\d{1,2}):(\d{2})")
SHOWS = "הופעות"  # the one category that is not sport


class TicketeamsSettings(SourceSettings):
    site_url: str = "https://ticketeams.co.il"
    max_pages: int = Field(30, ge=1)


@dataclass
class Term:
    name: str
    slug: str = ""
    parent: int = 0


@dataclass
class Terms:
    """Taxonomy -> term id -> term."""

    by_tax: dict[str, dict[int, Term]] = field(default_factory=dict)

    def names(self, tax: str, ids: Any) -> list[str]:
        terms = self.by_tax.get(tax, {})
        return [terms[i].name for i in ids or [] if i in terms]

    def first(self, tax: str, ids: Any) -> Term | None:
        terms = self.by_tax.get(tax, {})
        return next((terms[i] for i in ids or [] if i in terms), None)

    def categories(self, ids: Any) -> list[str]:
        """The product's categories with their parents ("ליגה אנגלית", "כדורגל")."""
        cats, seen, out = self.by_tax.get("product_cat", {}), set(), []
        for i in ids or []:
            while i in cats and i not in seen:
                seen.add(i)
                out.append(cats[i].name)
                i = cats[i].parent
        return out


class TicketeamsScraper(BaseScraper[TicketeamsSettings]):
    name = "ticketeams"
    settings_model = TicketeamsSettings
    abroad_only = True

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        terms = Terms({tax: parse_terms(await self._list(tax, "id,name,slug,parent")) for tax in TAXONOMIES})
        products = await self._list("product", "id,link,title," + ",".join(TAXONOMIES))
        self.log.info("listed %d products", len(products))
        now = datetime.now().astimezone()

        async def detail(product: dict[str, Any]) -> ScrapedEvent | None:
            event = parse_product(await self.get_text(product["link"]), product, terms, self.timezone)
            return event if event and event.start_at and event.start_at > now else None

        listed = [p for p in products if isinstance(p, dict) and p.get("id") and p.get("link")]
        async for event in self.stream_isolated(listed, detail, label=lambda p: p["link"]):
            yield event

    async def _list(self, kind: str, fields: str) -> list[Any]:
        async def page(index: int) -> tuple[list[Any], int | None]:
            params = {"per_page": PAGE_SIZE, "page": index + 1, "_fields": fields, "orderby": "id", "order": "asc"}
            response = await self.request("GET", f"{self.settings.site_url}/wp-json/wp/v2/{kind}", params=params)
            try:
                items = response.json()
            except ValueError as exc:
                raise ParseError(f"{response.request.url}: expected JSON") from exc
            if not isinstance(items, list):
                raise ParseError(f"{response.request.url}: expected a list")
            return items, to_int(response.headers.get("X-WP-Total"))

        return await self.paginate(page, PAGE_SIZE, self.settings.max_pages)


def parse_terms(items: list[Any]) -> dict[int, Term]:
    return {
        t["id"]: Term(html_to_text(t["name"]) or t["name"], unquote(t.get("slug") or ""), t.get("parent") or 0)
        for t in items
        if isinstance(t, dict) and t.get("id") and t.get("name")
    }


def parse_product(page_html: str, product: dict[str, Any], terms: Terms, timezone: str) -> ScrapedEvent | None:
    """One product page -> its event; None when the page shows no date (not a match or a show)."""
    url = product["link"]
    tree = LexborHTMLParser(page_html)
    date = next((m for n in tree.css(".jet-listing-dynamic-field__content") if (m := _DATE.search(n.text()))), None)
    if date is None:
        return None
    time = next((m for n in tree.css(".elementor-icon-box-title") if (m := _TIME.fullmatch(n.text().strip()))), None)
    country_name = (terms.first("countrys", product.get("countrys")) or Term("")).name
    country = country_code(country_name)
    city = terms.first("cities", product.get("cities"))
    city_en = city.slug.replace("-", " ") if city and city.slug.isascii() and not city.slug.isdigit() else None
    tz = zone(local_zone(country, timezone, city_en))  # the page shows the venue's local time
    hour, minute = (int(time[1]), int(time[2])) if time else (0, 0)
    start_at = datetime(int(date[3]), _MONTHS[date[1]], int(date[2]), hour, minute, tzinfo=tz)

    offers: dict[str, Any] = {}
    image = None
    for node in jsonld_nodes(page_html):
        if "product" in schema_types(node):
            if isinstance(node.get("offers"), dict):
                offers = node["offers"]
            # The product's own picture. og:image is no use: most products have none, and theirs is then
            # the site's background (רקע.png, 936 of 990 products, Oct 2026) or its logo.
            pictures = node.get("image") if isinstance(node.get("image"), list) else [node.get("image")]
            image = next((u for p in pictures if (u := p.get("url") if isinstance(p, dict) else p) and isinstance(u, str)), None)
    low, high = to_decimal(offers.get("lowPrice") or offers.get("price")), to_decimal(offers.get("highPrice"))
    venue = terms.first("stadiums", product.get("stadiums")) or terms.first("halls", product.get("halls"))
    categories = terms.categories(product.get("product_cat"))
    genres = categories if SHOWS in categories else [*categories, "ספורט"]
    title = html_to_text((product.get("title") or {}).get("rendered")) or ""
    if not title:
        raise ParseError(f"{url}: product without a title")
    return ScrapedEvent(
        source=TicketeamsScraper.name,
        source_event_id=str(product["id"]),
        url=url,
        title=title,
        start_at=start_at,
        venue_name=venue.name if venue else None,
        city=city.name if city else None,
        country=None if country == "IL" else country,  # country_code gives "ZZ" when unknown
        source_genres=list(dict.fromkeys(genres)),
        artists=terms.names("arts", product.get("arts")),
        images=[image] if image else [],
        currency=clean_str(offers.get("priceCurrency")) or "EUR",
        min_price=low or None,
        max_price=high or low or None,
        is_sold_out="outofstock" in str(offers.get("availability", "")).lower(),
        extra={"time_known": time is not None},
    )
