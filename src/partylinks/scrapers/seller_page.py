"""Prices on a ticket seller's own event page, for guides that only link to it (a city calendar's
"הזמנת כרטיסים" button, Nov 2026):

- the page's JSON-LD ``offers`` (Tickchak white labels: event.matnasbinyamin.co.il, ...);
- Coing's "מחיר" row (coing.co and its white labels: events.kehilatayim.org.il): "190 ש"ח(תושב 165 ש"ח)",
  "ללא עלות, בהרשמה מראש";
- a buy button that names the price ("בחירת כרטיסים ב-₪35": Mevalim's box offices).

Nothing else on the page is read: a seller's other text (other shows, add-ons) is no price of this event.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from selectolax.lexbor import LexborHTMLParser

from partylinks.models import TicketTier, TierStatus
from partylinks.parsing import clean_str, jsonld_nodes, schema_types, to_decimal
from partylinks.scrapers.text_prices import text_tickets

if TYPE_CHECKING:
    from partylinks.scrapers.base import BaseScraper

_GONE = ("soldout", "outofstock", "discontinued")
log = logging.getLogger(__name__)


async def fetch_seller_page(scraper: BaseScraper[Any], url: str) -> str | None:
    """The seller's page through a client of its own (the source's TLS and proxy), fetched once: a seller
    that refuses or throttles us (403 / 429) never moves the source to another proxy or stops its run."""
    try:
        async with scraper._new_client(scraper._proxy) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.text
    except Exception as exc:  # noqa: BLE001 - no price from it, the event stays as its guide lists it
        log.debug("%s: seller page %s: %r", scraper.name, url, exc)
        return None


def _offers(value: Any) -> list[dict[str, Any]]:
    """Offer / list of Offers / AggregateOffer, flat."""
    found: list[dict[str, Any]] = []
    for item in value if isinstance(value, list) else [value]:
        if isinstance(item, dict):
            nested = item.get("offers")
            found += _offers(nested) if nested else [item]
    return found


def _page_event(page_html: str, url: str | None) -> dict[str, Any] | None:
    """The page's own JSON-LD event: the one naming the page's id ("/113272"), else the only one when
    it names no page at all. A Tickchak page lists 30 other events beside its own (the first may be
    another), and a listing page ("/shows") holds some other show."""
    events = [n for n in jsonld_nodes(page_html) if "offers" in n and any("event" in t for t in schema_types(n))]
    key = urlsplit(url or "").path.rstrip("/").rsplit("/", 1)[-1]
    texts = [json.dumps(n, ensure_ascii=False) for n in events]
    mine = [n for n, t in zip(events, texts, strict=True) if key and re.search(rf"/{re.escape(key)}(?![\w-])", t)]
    if mine:
        return mine[0] if len(mine) == 1 else None
    return events[0] if len(events) == 1 and '"url":' not in texts[0] else None


def jsonld_tiers(page_html: str, url: str | None = None) -> list[TicketTier]:
    """One tier per JSON-LD offer of the page's event that states a price."""
    tiers = []
    event = _page_event(page_html, url)
    for offer in _offers(event.get("offers")) if event else []:
        price = to_decimal(offer.get("price") if offer.get("price") is not None else offer.get("lowPrice"))
        if price is None:
            continue
        availability = str(offer.get("availability") or "").rsplit("/", 1)[-1].replace("_", "").lower()
        tiers.append(
            TicketTier(
                name=clean_str(offer.get("name")) or "כרטיס",
                price=price,
                currency=clean_str(offer.get("priceCurrency")) or "ILS",
                is_free=price == 0,
                status=TierStatus.SOLD_OUT if availability in _GONE else TierStatus.ON_SALE,
            )
        )
    return tiers


def seller_tiers(page_html: str, url: str | None = None) -> list[TicketTier]:
    """The tiers the seller's event page at ``url`` states; [] when it states none."""
    if tiers := jsonld_tiers(page_html, url):
        return tiers
    tree = LexborHTMLParser(page_html)
    for col in tree.css(".info-col"):  # Coing
        label = col.css_first(".info-col-label")
        value = col.css_first(".info-col-value")
        if label and value and "מחיר" in label.text():
            return text_tickets("מחיר: " + " ".join(value.text(separator=" ").split()))
    for button in tree.css("button, a"):  # Mevalim: "בחירת כרטיסים ב-₪35"
        text = " ".join(button.text().split())
        if len(text) <= 40 and (tiers := text_tickets(text)):
            return tiers
    return []
