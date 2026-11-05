"""Eventim Israel city pages (eventim.co.il, barak-tickets.co.il): the events they list, as a fallback.

Akamai guards the event pages only: after ~13 it challenges, then refuses them (403), while the city / region
pages (``/city/<slug>-<id>/``, ``?page=N``) keep answering (Nov 2026; a real browser was refused too). Those
pages render their events server-side as a schema.org ``ItemList`` (``<script id="eventMarkup">``): name,
start / end, venue, city, low price, availability, kind, and ``offers.url`` the event page. An event whose page
could not be read is built from that item, so no event is lost to the block; it lacks the artwork and the
ticket rows until a run reads its page (its stored artwork is kept meanwhile).
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser

from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, parse_iso, to_decimal
from partylinks.scrapers.base import BaseScraper, SourceBlockedError

_EVENT_ID = re.compile(r"/event/[^/?#]*-(\d+)/?$")
_PAGE = re.compile(r'data-page="(\d+)"')

Listed = dict[str, tuple[str, dict[str, Any]]]  # event id -> (event page, its ItemList item)

# A listing item's schema.org kind -> a declared genre (event pages give the Eventim genres).
TYPE_GENRES = {
    "ChildrensEvent": "ילדים", "MusicEvent": "concert", "TheaterEvent": "תיאטרון", "ComedyEvent": "סטנדאפ",
    "DanceEvent": "מחול", "Festival": "פסטיבל", "SportsEvent": "ספורט", "LiteraryEvent": "הרצאה",
}  # fmt: skip


def event_id_of(url: str) -> str | None:
    return m.group(1) if (m := _EVENT_ID.search(url.split("?")[0])) else None


def parse_listing(page_html: str, site_url: str) -> Listed:
    """One city listing page's events, by event id: (event page, its ItemList item)."""
    found: Listed = {}
    for script in LexborHTMLParser(page_html).css('script[type="application/ld+json"]'):
        try:
            node = json.loads(script.text())
        except json.JSONDecodeError:
            continue
        if not isinstance(node, dict) or node.get("@type") != "ItemList":
            continue
        for entry in node.get("itemListElement") or []:
            item = entry.get("item") if isinstance(entry, dict) else None
            if not isinstance(item, dict):
                continue
            offers = item.get("offers")
            # item.url is often the artist page; offers.url is the event itself.
            for raw in (offers.get("url") if isinstance(offers, dict) else None, item.get("url")):
                if isinstance(raw, str) and (event_id := event_id_of(raw)):
                    found.setdefault(event_id, (urljoin(site_url, raw.split("?")[0]), item))
                    break
    return found


def last_page(page_html: str) -> int:
    return max((int(n) for n in _PAGE.findall(page_html)), default=1)


def listing_event(
    event_id: str, url: str, item: dict[str, Any], timezone: str, source: str, type_genres: dict[str, str]
) -> ScrapedEvent | None:
    """An event from its listing item alone (its page was refused): no artwork, no ticket rows."""
    name, start = clean_str(item.get("name")), parse_iso(item.get("startDate"), timezone)
    if not name or start is None:
        return None
    place = item.get("location") if isinstance(item.get("location"), dict) else {}
    address = place.get("address") if isinstance(place.get("address"), dict) else {}
    offers = item.get("offers") if isinstance(item.get("offers"), dict) else {}
    low = to_decimal(offers.get("lowPrice"))
    kind = type_genres.get(str(item.get("@type") or ""))
    return ScrapedEvent(
        source=source,
        source_event_id=event_id,
        url=url,
        title=name,
        start_at=start,
        end_at=parse_iso(item.get("endDate"), timezone),
        venue_name=clean_str(place.get("name")),
        city=clean_str(address.get("addressLocality")),
        source_genres=[kind] if kind else [],
        min_price=low if low else None,  # "0.0" is the listing's blank, not a free show
        is_sold_out=str(offers.get("availability") or "").endswith("SoldOut"),
        extra={"from_listing": True},
    )


async def details_or_listing(
    scraper: BaseScraper[Any],
    urls: list[str],
    fetch: Callable[[str], Awaitable[ScrapedEvent | None]],
    listed: Listed,
    type_genres: dict[str, str],
) -> AsyncIterator[ScrapedEvent]:
    """Each event page read in full while Akamai lets us; every event it refused (or that failed) from its
    listing item instead, with the artwork a past run stored. The refusal is still recorded: the run is then
    partial, so it never hides the sitemap's events the city pages do not list (only a clean run unlists).
    With no listing to fall back on, the run stops as blocked, as before."""
    done: set[str] = set()
    try:
        async for event in scraper.stream_isolated(urls, fetch, label=lambda u: u):
            done |= {event.source_event_id, event_id_of(str(event.url)) or ""}
            yield event
    except SourceBlockedError as exc:
        if not listed:
            raise
        scraper.record_failure("event pages", exc)
        scraper.log.warning("event pages refused (%s): the rest come from the city pages", exc)
    for url in urls:
        event_id = event_id_of(url)
        if not event_id or event_id in done or event_id not in listed:
            continue
        page, item = listed[event_id]
        if event := listing_event(event_id, page, item, scraper.timezone, scraper.name, type_genres):
            if known := scraper.known_images.get(event_id):
                event.image_url, event.images = known[0], known
            yield event
