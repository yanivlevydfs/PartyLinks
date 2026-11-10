"""Eventim Israel's public search API (``public-api.eventim.com``): every event, as the site's own search box reads it.

Akamai challenges eventim.co.il's event pages (``sec-if-cpt``) from our VPN and evomi IPs on the first
request, and from any IP after ~20 (Nov 2026); this API, which the site's search widget calls from the
browser, answers any client. ``/v1/products`` needs a filter: one call with ``city_names=*`` returns no
events but the facet of every city, then each city is read page by page. Each product carries its
event page (``link``, the id the sitemap uses), name, start, venue with coordinates, city, categories,
artists, the lowest price, stock and status, and a 222px teaser whose full artwork sits at
``teaser/evo/artwork/<year>/<name>-artwork.<ext>``. It covered every non-Zappa sitemap event (447 of 447,
Nov 2026). No ticket rows: those still come from the event pages Akamai lets through.
"""

from __future__ import annotations

from typing import Any

from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, parse_iso, to_decimal
from partylinks.scrapers.base import BaseScraper
from partylinks.scrapers.eventim_listing import Listed, event_id_of


async def list_products(scraper: BaseScraper[Any], api_url: str, web_id: str, language: str, page_size: int) -> Listed:
    """Every product of every city, by event id: (event page, product)."""
    base = {"webId": web_id, "language": language, "page_size": page_size}
    seed = await scraper.get_json(api_url, params=base | {"city_names": "*"}, expect=dict)
    cities = [
        c["value"] for f in seed.get("facets") or [] if f.get("name") == "cities" for c in f.get("facetItems") or []
    ]
    found: Listed = {}
    for city in cities:
        page, pages = 1, 1
        while page <= pages:
            data = await scraper.get_json(api_url, params=base | {"city_names": city, "page": page}, expect=dict)
            pages, page = int(data.get("totalPages") or 0), page + 1
            for product in data.get("products") or []:
                link = str(product.get("link") or "")
                if event_id := event_id_of(link):
                    found.setdefault(event_id, (link, product))
    return found


def artwork_of(teaser: str) -> str:
    # ponytail: Eventim's file naming, not an API field; a 404 leaves the teaser (the next image) shown.
    return teaser.replace("/teaser/222x222/", "/teaser/evo/artwork/").replace("-teaser.", "-artwork.")


def product_event(event_id: str, url: str, product: dict[str, Any], timezone: str, source: str) -> ScrapedEvent | None:
    """An event from its API product (its page was refused); None when cancelled or undated."""
    live = (product.get("typeAttributes") or {}).get("liveEntertainment") or {}
    name, start = clean_str(product.get("name")), parse_iso(live.get("startDate"), timezone)
    if not name or start is None or product.get("status") == "Cancelled":
        return None
    place = live.get("location") or {}
    geo = place.get("geoLocation") or {}
    teaser = clean_str(product.get("imageUrl"))
    price = to_decimal(product.get("price"))
    categories = [c for k in product.get("categories") or [] for c in (k.get("parentCategory") or {}, k)]
    return ScrapedEvent(
        source=source,
        source_event_id=event_id,
        url=url,
        title=name,
        start_at=start,
        venue_name=clean_str(place.get("name")),
        city=clean_str(place.get("city")),
        latitude=geo.get("latitude"),
        longitude=geo.get("longitude"),
        source_genres=list(dict.fromkeys(n for c in categories if (n := clean_str(c.get("name"))))),
        artists=[n for a in product.get("attractions") or [] if (n := clean_str(a.get("name")))],
        images=[artwork_of(teaser), teaser] if teaser else [],
        min_price=price,
        is_sold_out=product.get("inStock") is False,
        extra={"from_listing": True, "series_id": clean_str(product.get("productGroupId"))},
    )
