"""Barak Tickets (barak-tickets.co.il) - a Haifa-based box office: concerts, theatre, stand-up, kids.

The site is an Eventim Israel tenant (same Akamai, same page templates, same event ids), but it has
no sitemap (robots.txt: "Sitemap: https:// #Sitemap-URL einfügen") and its category pages fill
from ``/api/exploration/v1``, which robots.txt disallows. Listing: the city/region pages
(``/city/<slug>-<id>/``, ``?page=N``) render their events server-side as a schema.org
``ItemList`` (``<script id="eventMarkup">``), each item's ``offers.url`` the event page
``/event/<slug>-<id>/``; page links carry ``data-page``. Details: every event page is an Eventim
page (``dataLayer`` pageMeta, ticket rows, og:image), read with the Eventim parser.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from urllib.parse import urljoin

import httpx
from pydantic import Field
from selectolax.lexbor import LexborHTMLParser

from partylinks.models import ScrapedEvent
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.eventim import parse_event

_EVENT_ID = re.compile(r"/event/[^/?#]*-(\d+)/?$")
_PAGE = re.compile(r'data-page="(\d+)"')


class BarakSettings(SourceSettings):
    site_url: str
    # Region pages that list the events (/city/<slug>-<id>/); the site's nav shows ten.
    city_paths: list[str]
    max_pages: int = Field(20, ge=1)  # per city
    max_events: int = Field(800, ge=1)


class BarakScraper(BaseScraper[BarakSettings]):
    name = "barak"
    settings_model = BarakSettings

    def is_blocked(self, response: httpx.Response) -> bool:
        """Akamai (as on eventim.co.il) answers 403 / 429 once it rate-limits us."""
        return response.status_code in (403, 429)

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        urls: dict[str, str] = {}  # event id -> url; regions overlap
        for path in self.settings.city_paths:
            urls.update(await self.optional(f"city {path}", self._list_city(path)) or {})
        if not urls and self.failures:
            raise RuntimeError(f"{self.name}: every city page failed")
        found = list(urls.values())
        if len(found) > self.settings.max_events:
            self.log.warning("listing truncated at max_events=%d (%d found)", self.settings.max_events, len(found))
        self.log.info("listed %d events", len(found))
        async for event in self.stream_isolated(
            found[: self.settings.max_events], self._fetch_event, label=lambda u: u
        ):
            yield event

    async def _list_city(self, path: str) -> dict[str, str]:
        s = self.settings
        url = urljoin(s.site_url, path)
        page_html = await self.get_text(url)
        urls, last = parse_city(page_html, s.site_url), last_page(page_html)
        for page in range(2, min(last, s.max_pages) + 1):
            more = await self.optional(f"city {path} page {page}", self.get_text(url, params={"page": page}))
            urls.update(parse_city(more or "", s.site_url))
        return urls

    async def _fetch_event(self, url: str) -> ScrapedEvent | None:
        return parse_event(await self.get_text(url), url, self.timezone, self.name)


def parse_city(page_html: str, site_url: str) -> dict[str, str]:
    """Event pages of one city listing page, by event id, from its ``eventMarkup`` ItemList."""
    found: dict[str, str] = {}
    for script in LexborHTMLParser(page_html).css('script[type="application/ld+json"]'):
        try:
            node = json.loads(script.text())
        except json.JSONDecodeError:
            continue
        if not isinstance(node, dict) or node.get("@type") != "ItemList":
            continue
        for entry in node.get("itemListElement") or []:
            item = entry.get("item") if isinstance(entry, dict) else None
            offers = item.get("offers") if isinstance(item, dict) else None
            # item.url is often the artist page; offers.url is the event itself.
            for raw in (
                offers.get("url") if isinstance(offers, dict) else None,
                item.get("url") if isinstance(item, dict) else None,
            ):
                if isinstance(raw, str) and (m := _EVENT_ID.search(raw.split("?")[0])):
                    found.setdefault(m.group(1), urljoin(site_url, raw.split("?")[0]))
                    break
    return found


def last_page(page_html: str) -> int:
    return max((int(n) for n in _PAGE.findall(page_html)), default=1)
