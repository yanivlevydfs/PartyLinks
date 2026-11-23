"""Barak Tickets (barak-tickets.co.il) - a Haifa-based box office: concerts, theatre, stand-up, kids.

The site is an Eventim Israel tenant (same Akamai, same page templates, same event ids), but it has
no sitemap (robots.txt: "Sitemap: https:// #Sitemap-URL einfügen") and its category pages fill
from ``/api/exploration/v1``, which robots.txt disallows. Listing: the city/region pages
(``/city/<slug>-<id>/``, ``?page=N``; page links carry ``data-page``). Their schema.org ``ItemList``
(``<script id="eventMarkup">``, each item's ``offers.url`` the event page ``/event/<slug>-<id>/``) is on
the first page only (~22 events); every page also prerenders the listing widget's buttons: per show its
group page (``/artist/...`` or ``/eventseries/...``) then its ``/event/<slug>-<id>/`` links, read from all
pages (~330 events over the ten regions against ~130 in the ItemLists, Nov 2026). Binyamina, Zichron
Yaakov and Caesarea answer every ``?page=N`` with the same 32 events.
Details: every event page is an Eventim page (``dataLayer`` pageMeta, ticket rows, og:image), read with
the Eventim parser. Akamai's behavioural challenge (200, "sec-if-cpt") answers the event and group pages
after ~10 from any IP: Railway's, evomi's, a free Israeli proxy's, a home IP in Germany; our VPN gets 403 at
once (Nov 2026). Each refused page is built from Eventim's public search API (``api_url``, eventim_api), which
the site's own search widget calls with eventim.co.il's web id: it lists every Barak event by the same id
(305 of 305, Nov 2026), with artwork, venue pin, artists and the lowest price, so a refusal loses nothing
and the run stays clean. The event keeps its barak-tickets.co.il page; a region city ("חיפה והצפון") becomes
the ItemList's town, else the town at the venue's pin (as ticket4u). Without the API (or for an event it
lacks): the ItemList item, else one from the show's group page, whose JSON-LD ``EventSeries.subEvent`` (an
artist page carries its first 20 dates) gives name, dates, venue, city, kind and a teaser image.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import unquote, urljoin

import httpx
from pydantic import Field

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, jsonld_nodes
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceBlockedError, SourceSettings
from partylinks.scrapers.eventim import parse_event
from partylinks.scrapers.eventim_api import list_products, product_event
from partylinks.scrapers.eventim_listing import (
    TYPE_GENRES,
    Listed,
    details_or_listing,
    event_id_of,
    last_page,
    listing_event,
    parse_listing,
)
from partylinks.scrapers.ticket4u import is_region, town_at

__all__ = ["BarakScraper", "BarakSettings", "last_page", "listing_image", "parse_city", "parse_group", "widget_links"]

_HREF = re.compile(r'href="(/(?:event|artist|eventseries)/[^"?#]+/)')

Links = dict[str, tuple[str, str | None]]  # event id -> (event page, its show's group page)


class BarakSettings(SourceSettings):
    site_url: str
    # Region pages that list the events (/city/<slug>-<id>/); the site's nav shows ten.
    city_paths: list[str]
    max_pages: int = Field(20, ge=1)  # per city
    max_events: int = Field(800, ge=1)
    # Group pages (artist / event series) read for the events no ItemList carries: their listing item.
    read_groups: bool = True
    # A listing item's schema.org kind -> a declared genre, for events built from the listing.
    type_genres: dict[str, str] = Field(default_factory=lambda: dict(TYPE_GENRES))
    # A listing item's picture path -> the one served: the "galery" copy answers 404 for every Barak event
    # (104 of 104 stored rows, Nov 2026), the same file under "teaser" is there.
    image_paths: dict[str, str] = Field(default_factory=lambda: {"/IL-eventim/galery/": "/IL-eventim/teaser/"})
    # Eventim's public search API (eventim_api): each event whose page Akamai refuses comes from its product.
    # Empty: no API (refused events come from the ItemList / group pages, and the run is partial).
    api_url: str = ""
    api_web_id: str = ""
    api_language: str = "iw"
    api_page_size: int = Field(100, ge=1, le=100)


class BarakScraper(BaseScraper[BarakSettings]):
    name = "barak"
    settings_model = BarakSettings

    def is_blocked(self, response: httpx.Response) -> bool:
        """Akamai (as on eventim.co.il) answers 403 / 429 once it rate-limits us, or 200 with its
        behavioural challenge (2.7 kB, "sec-if-cpt") after ~3 event pages (Nov 2026: 120 such pages a run)."""
        return response.status_code in (403, 429) or b"sec-if-cpt" in response.content[:4000]

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        items: Listed = {}  # the ItemLists' (regions overlap)
        links: Links = {}
        for path in s.city_paths:
            found, more = await self.optional(f"city {path}", self._list_city(path)) or ({}, {})
            items.update(found)
            links.update(more)
        if not links and self.failures:
            raise RuntimeError(f"{self.name}: every city page failed")
        products: Listed = {}
        if s.api_url:
            api = list_products(self, s.api_url, s.api_web_id, s.api_language, s.api_page_size)
            products = await self.optional("api", api) or {}
        # The API's product first (artwork, pin), under the Barak page (the product links to eventim.co.il).
        listed: Listed = {i: (page, products[i][1]) for i, (page, _) in links.items() if i in products}
        for event_id, entry in items.items():
            listed.setdefault(event_id, entry)
        if s.read_groups:
            await self._read_groups(links, listed)
        urls = [url for url, _ in links.values()][: s.max_events]
        if len(links) > s.max_events:
            self.log.warning("listing truncated at max_events=%d (%d found)", s.max_events, len(links))
        self.log.debug(
            "listed %d events (%d with a listing item, %d from the API)", len(links), len(listed), len(products)
        )

        def build(event_id: str, url: str, item: dict[str, Any]) -> ScrapedEvent | None:
            if "productId" not in item:
                return listing_event(event_id, url, item, self.timezone, self.name, s.type_genres)
            event = product_event(event_id, url, item, self.timezone, self.name)
            if event is None:
                return None
            local = items.get(event_id, ("", {}))[1]  # the ItemList's item, when it lists the event
            # A product without categories (26 of 303, Nov 2026): the ItemList's schema.org kind.
            if not event.source_genres and (kind := s.type_genres.get(str(local.get("@type") or ""))):
                event.source_genres = [kind]
            if is_region(event.city):
                place = local.get("location")
                address = place.get("address") if isinstance(place, dict) else None
                town = clean_str(address.get("addressLocality")) if isinstance(address, dict) else None
                event.city = town if town and not is_region(town) else town_at(event.latitude, event.longitude)
            return event

        # With the API every refused page has its product: a refusal loses nothing, the run stays clean.
        lost_only = bool(products)
        events = details_or_listing(self, urls, self._fetch_event, listed, s.type_genres, build, lost_only=lost_only)
        async for event in events:
            # No stored artwork: the group's teaser.
            item = listed[event.source_event_id][1] if event.extra.get("from_listing") and not event.images else {}
            if image := clean_str(item.get("image")):
                image = listing_image(image, s.image_paths)
                event.image_url, event.images = image, [image]
            if is_current(
                event.start_at, event.end_at, RetentionSettings(), self.timezone, time_known=event.time_known
            ):
                yield event

    async def _list_city(self, path: str) -> tuple[Listed, Links]:
        """One region's events: the first page's ItemList items, and every page's event links."""
        s = self.settings
        url = urljoin(s.site_url, path)
        page_html = await self.get_text(url)
        found, last = parse_listing(page_html, s.site_url), last_page(page_html)
        links = widget_links(page_html, s.site_url)
        if last > s.max_pages:
            self.log.warning("city %s truncated at max_pages=%d (%d pages)", path, s.max_pages, last)
        for page in range(2, min(last, s.max_pages) + 1):
            more = await self.optional(f"city {path} page {page}", self.get_text(url, params={"page": page})) or ""
            found.update(parse_listing(more, s.site_url))
            for event_id, link in widget_links(more, s.site_url).items():
                links.setdefault(event_id, link)
        for event_id, (page, _) in found.items():
            links.setdefault(event_id, (page, None))
        return found, links

    async def _read_groups(self, links: Links, listed: Listed) -> None:
        """A listing item for each event no ItemList carries, from its show's group page. Akamai challenges
        these pages too once it has seen enough of us: the rest then go to their event pages (the run is partial)."""
        groups = dict.fromkeys(g for event_id, (_, g) in links.items() if g and event_id not in listed)
        try:
            for group in groups:
                page_html = await self.optional(f"group {group}", self.get_text(urljoin(self.settings.site_url, group)))
                for event_id, entry in parse_group(page_html or "", self.settings.site_url).items():
                    listed.setdefault(event_id, entry)
        except SourceBlockedError as exc:
            self.record_failure("group pages", exc)
            self.log.warning("group pages refused (%s)", exc)

    async def _fetch_event(self, url: str) -> ScrapedEvent | None:
        return parse_event(await self.get_text(url), url, self.timezone, self.name)


def widget_links(page_html: str, site_url: str) -> Links:
    """One city listing page's events from the listing widget's prerendered buttons, by event id: the
    event page and the group page (artist / event series) of the show it comes under."""
    found: Links = {}
    group = None
    for href in map(unquote, _HREF.findall(page_html)):
        if not href.startswith("/event/"):
            group = href
        elif event_id := event_id_of(href):
            found.setdefault(event_id, (urljoin(site_url, href), group))
    return found


def listing_image(url: str, paths: dict[str, str]) -> str:
    """The listing item's picture at the path the site serves it (``image_paths``)."""
    for listed, served in paths.items():
        url = url.replace(listed, served)
    return url


def parse_group(page_html: str, site_url: str) -> Listed:
    """A group page's dates (JSON-LD ``EventSeries.subEvent``, each with a single ``Offer``) as listing
    items, by event id, in the ItemList item's shape (``offers`` one object)."""
    found: Listed = {}
    for node in jsonld_nodes(page_html):
        offers = node.get("offers")
        offer = next((o for o in offers if isinstance(o, dict)), None) if isinstance(offers, list) else offers
        url = offer.get("url") if isinstance(offer, dict) else None
        if node.get("startDate") and isinstance(url, str) and (event_id := event_id_of(url)):
            item: dict[str, Any] = {**node, "offers": offer}
            found.setdefault(event_id, (urljoin(site_url, url.split("?")[0]), item))
    return found


def parse_city(page_html: str, site_url: str) -> dict[str, str]:
    """Event pages of one city listing page, by event id, from its ``eventMarkup`` ItemList."""
    return {event_id: url for event_id, (url, _) in parse_listing(page_html, site_url).items()}
