"""Barak Tickets (barak-tickets.co.il) - a Haifa-based box office: concerts, theatre, stand-up, kids.

The site is an Eventim Israel tenant (same Akamai, same page templates, same event ids), but it has
no sitemap (robots.txt: "Sitemap: https:// #Sitemap-URL einfügen") and its category pages fill
from ``/api/exploration/v1``, which robots.txt disallows. Listing: the city/region pages
(``/city/<slug>-<id>/``, ``?page=N``) render their events server-side as a schema.org
``ItemList`` (``<script id="eventMarkup">``), each item's ``offers.url`` the event page
``/event/<slug>-<id>/``; page links carry ``data-page``. Details: every event page is an Eventim
page (``dataLayer`` pageMeta, ticket rows, og:image), read with the Eventim parser. Pages Akamai refuses
are built from their listing item (``eventim_listing``), so no event is lost to the block.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from urllib.parse import urljoin

import httpx
from pydantic import Field

from partylinks.models import ScrapedEvent
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.eventim import parse_event
from partylinks.scrapers.eventim_listing import TYPE_GENRES, Listed, details_or_listing, last_page, parse_listing

__all__ = ["BarakScraper", "BarakSettings", "last_page", "parse_city"]


class BarakSettings(SourceSettings):
    site_url: str
    # Region pages that list the events (/city/<slug>-<id>/); the site's nav shows ten.
    city_paths: list[str]
    max_pages: int = Field(20, ge=1)  # per city
    max_events: int = Field(800, ge=1)
    # A listing item's schema.org kind -> a declared genre, for events built from the listing.
    type_genres: dict[str, str] = Field(default_factory=lambda: dict(TYPE_GENRES))


class BarakScraper(BaseScraper[BarakSettings]):
    name = "barak"
    settings_model = BarakSettings

    def is_blocked(self, response: httpx.Response) -> bool:
        """Akamai (as on eventim.co.il) answers 403 / 429 once it rate-limits us, or 200 with its
        behavioural challenge (2.7 kB, "sec-if-cpt") after ~3 event pages (Nov 2026: 120 such pages a run)."""
        return response.status_code in (403, 429) or b"sec-if-cpt" in response.content[:4000]

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        listed: Listed = {}  # regions overlap
        for path in self.settings.city_paths:
            listed.update(await self.optional(f"city {path}", self._list_city(path)) or {})
        if not listed and self.failures:
            raise RuntimeError(f"{self.name}: every city page failed")
        urls = [url for url, _ in listed.values()][: self.settings.max_events]
        if len(listed) > self.settings.max_events:
            self.log.warning("listing truncated at max_events=%d (%d found)", self.settings.max_events, len(listed))
        self.log.debug("listed %d events", len(listed))
        async for event in details_or_listing(self, urls, self._fetch_event, listed, self.settings.type_genres):
            yield event

    async def _list_city(self, path: str) -> Listed:
        s = self.settings
        url = urljoin(s.site_url, path)
        page_html = await self.get_text(url)
        found, last = parse_listing(page_html, s.site_url), last_page(page_html)
        for page in range(2, min(last, s.max_pages) + 1):
            more = await self.optional(f"city {path} page {page}", self.get_text(url, params={"page": page}))
            found.update(parse_listing(more or "", s.site_url))
        return found

    async def _fetch_event(self, url: str) -> ScrapedEvent | None:
        return parse_event(await self.get_text(url), url, self.timezone, self.name)


def parse_city(page_html: str, site_url: str) -> dict[str, str]:
    """Event pages of one city listing page, by event id, from its ``eventMarkup`` ItemList."""
    return {event_id: url for event_id, (url, _) in parse_listing(page_html, site_url).items()}
