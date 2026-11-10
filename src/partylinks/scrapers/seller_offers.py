"""Prices and artwork from the seller's own page, for guides that link out without them (Makore, OGO).

A guide's event often has no price or picture of its own (Nov 2026: Makore 4,700 of 6,300 upcoming
without a price, OGO 1,850 of 5,800), but its ticket link (``extra["purchase_url"]``) leads to a
seller that has both. Read there, in the order that gives real data:

- bookit.fun (an SPA, Makore's biggest seller): ``POST /api/product/id`` returns the product's
  price list per date (``Availability``) and its pictures.
- Smarticket tenants (``<name>.smarticket.co.il``): ``/api/shows`` once per host and run, parsed
  with the Smarticket scraper's own code; the performance is the link's id or the event's start.
- Any other seller: its page's schema.org ``Event`` (funzing, tickchak, smarticket's own domains...).

Only what the seller states is taken: no price is guessed from free text on a seller page (a city
hall's page lists every event's price), and no og:image (often the seller's logo). A host that gave
nothing ``seller_host_misses`` times in a row is not asked again in that run.
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel, Field
from selectolax.lexbor import LexborHTMLParser

from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import clean_str, jsonld_nodes, schema_types, to_decimal, zone
from partylinks.scrapers.chillz import EVENT_TYPES, parse_page

if TYPE_CHECKING:
    from partylinks.scrapers.base import BaseScraper

_SMARTICKET = re.compile(r"^[a-z0-9-]+\.smarticket\.co\.il$")
_SMARTICKET_ID = re.compile(r"/event/(\d+)")
BOOKIT_API = "https://bookit.fun/api/product/id"


class SellerLookupSettings(BaseModel):
    """Fields of a guide's source block (admin-tunable like the rest of it)."""

    # Read the seller's page for an event the guide lists without a price or a picture.
    seller_lookup: bool = True
    # A seller host that gave nothing this many times in a row is skipped for the rest of the run.
    seller_host_misses: int = Field(3, ge=1)


def priced(event: ScrapedEvent) -> bool:
    return event.min_price is not None or any(t.price is not None or t.is_free for t in event.tickets)


def with_tickets(event: ScrapedEvent, tickets: list[TicketTier]) -> ScrapedEvent:
    """``event`` with these tiers as its price (re-validated, so derived fields follow)."""
    prices = [t.price for t in tickets if t.price is not None]
    return ScrapedEvent.model_validate(
        event.model_dump()
        | {
            "tickets": [t.model_dump() for t in tickets],
            "min_price": min(prices, default=None),
            "max_price": max(prices, default=None),
            "currency": next((t.currency for t in tickets), event.currency),
        }
    )


def bookit_tickets(product: dict[str, Any], day: str | None) -> list[TicketTier]:
    """The product's tiers on ``day`` (YYYY-MM-DD); its stated minimum when that day is not on sale yet."""
    slots = [a for a in product.get("Availability") or [] if isinstance(a, dict)]
    rows = next((a.get("prices") or [] for a in slots if a.get("date") == day), [])
    tiers = [
        TicketTier(name=clean_str(r.get("name")) or "כרטיס", price=price, is_free=price == 0)
        for r in rows
        if isinstance(r, dict) and (price := to_decimal(r.get("amount"))) is not None
    ]
    # "Child under 6: 0" beside paid tiers is not free entry.
    paid = [t for t in tiers if t.price]
    if paid or tiers:
        return paid or tiers
    minimum = to_decimal(product.get("MinimalPrice"))
    return [TicketTier(name="החל מ", price=minimum)] if minimum else []


def bookit_images(product: dict[str, Any]) -> list[str]:
    media = sorted(
        (m for m in product.get("ProductMediaItems") or [] if isinstance(m, dict)), key=lambda m: not m.get("IsMain")
    )
    return [u for m in media if (u := clean_str(m.get("HighResolutionLink") or m.get("MediumResolutionLink")))]


def page_offer(page_html: str, url: str, timezone: str) -> tuple[list[TicketTier], list[str]]:
    """Tiers and pictures of the one schema.org Event on a seller page (none for a page of many)."""
    if sum(1 for n in jsonld_nodes(page_html) if schema_types(n) & EVENT_TYPES and n.get("name")) != 1:
        return [], []
    found = parse_page(page_html, url, timezone)
    if found is None:
        return [], []
    og = LexborHTMLParser(page_html).css_first('meta[property="og:image"]')
    og_url = og.attributes.get("content") if og else None
    tiers = [t.model_copy(update={"sale_start": None, "sale_end": None}) for t in found.tickets if t.price is not None]
    return tiers, [i for i in found.images if i != og_url]


class SellerLookup:
    """One per run of a guide: caches per-host feeds and gives up on hosts that have nothing."""

    def __init__(self, scraper: BaseScraper[Any], settings: SellerLookupSettings) -> None:
        self.scraper = scraper
        self.settings = settings
        self._shows: dict[str, asyncio.Task[list[ScrapedEvent]]] = {}
        self._products: dict[str, asyncio.Task[dict[str, Any]]] = {}
        self._misses: Counter[str] = Counter()
        self._hits: set[str] = set()
        self.filled = Counter[str]()  # "price" / "image": events completed this run (logged by the guide)

    async def fill(self, event: ScrapedEvent) -> ScrapedEvent:
        """``event`` with the seller's price and pictures where it has none; unchanged on any failure."""
        url = clean_str(event.extra.get("purchase_url"))
        host = (urlsplit(url).hostname or "").lower() if url else ""
        if not self.settings.seller_lookup or not host or (priced(event) and event.images):
            return event
        if host not in self._hits and self._misses[host] >= self.settings.seller_host_misses:
            return event
        try:
            tickets, images = await self._offer(url or "", host, event)
        except Exception as exc:  # noqa: BLE001 - the seller is extra data: never fails the guide's event
            self.scraper.log.debug("seller %s: %s: %s", url, type(exc).__name__, exc)
            tickets, images = [], []
        useful = (tickets and not priced(event)) or (images and not event.images)
        if not useful:
            self._misses[host] += 1
            return event
        self._hits.add(host)
        if tickets and not priced(event):
            event = with_tickets(event, tickets)
            self.filled["price"] += 1
        if images and not event.images:
            event = ScrapedEvent.model_validate(event.model_dump() | {"images": images, "image_url": None})
            self.filled["image"] += 1
        return event

    async def _offer(self, url: str, host: str, event: ScrapedEvent) -> tuple[list[TicketTier], list[str]]:
        tz = self.scraper.timezone
        if host.endswith("bookit.fun"):
            product_id = (parse_qs(urlsplit(url).query).get("productID") or [""])[0]
            if not product_id:
                return [], []
            product = await self._cached(self._products, product_id, lambda: self._bookit(product_id))
            day = event.start_at.astimezone(zone(tz)).date().isoformat() if event.start_at else None
            return bookit_tickets(product, day), bookit_images(product)
        if _SMARTICKET.match(host):
            performance = self._match(await self._cached(self._shows, host, lambda: self._smarticket(host)), url, event)
            return (performance.tickets, performance.images) if performance else ([], [])
        return page_offer(await self.scraper.get_text(url), url, tz)

    @staticmethod
    async def _cached[T](cache: dict[str, asyncio.Task[T]], key: str, call: Callable[[], Awaitable[T]]) -> T:
        """One request per key and run, shared by the events that ask at once."""
        if key not in cache:
            cache[key] = asyncio.ensure_future(call())
        return await cache[key]

    async def _bookit(self, product_id: str) -> dict[str, Any]:
        return await self.scraper.post_json(
            BOOKIT_API, json={"ProductID": product_id, "AffiliateId": "", "IsDirect": False}, expect=dict
        )

    async def _smarticket(self, host: str) -> list[ScrapedEvent]:
        # Local: partylinks.scrapers imports every scraper, the guides that import this module too.
        from partylinks.config import get_settings
        from partylinks.scrapers import source_settings
        from partylinks.scrapers.smarticket import (
            SmarticketSettings,
            SmarticketTenant,
            iter_performances,
            parse_event,
            parse_shows_body,
        )

        s = source_settings("smarticket", get_settings())
        assert isinstance(s, SmarticketSettings)
        shows = parse_shows_body(await self.scraper.get_text(f"https://{host}{s.shows_path}"), host)
        tenant = SmarticketTenant(host=host)
        found = []
        for show, performance in iter_performances(shows):
            try:
                if (e := parse_event(show, performance, tenant, s, self.scraper.timezone)) is not None:
                    found.append(e)
            except Exception:  # noqa: BLE001, S112 - one odd performance leaves the others usable
                continue
        return found

    @staticmethod
    def _match(performances: list[ScrapedEvent], url: str, event: ScrapedEvent) -> ScrapedEvent | None:
        """The performance the link names (``/event/<id>``, ``?id=<id>``), else the one at the event's start."""
        wanted = _SMARTICKET_ID.search(urlsplit(url).path)
        perf_id = wanted.group(1) if wanted else (parse_qs(urlsplit(url).query).get("id") or [None])[0]
        if perf_id:
            named = [p for p in performances if p.source_event_id.endswith(f":{perf_id}")]
            if named:
                return named[0]
        same = [p for p in performances if event.start_at and p.start_at == event.start_at]
        return same[0] if len(same) == 1 else None


def free_tier() -> TicketTier:
    return TicketTier(name="כניסה חופשית", price=Decimal(0), is_free=True)
