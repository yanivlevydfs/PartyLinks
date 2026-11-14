"""OGO abroad (us.ogo.events, de.ogo.events) - OGO's guides to cultural events in the USA and Germany.

The same platform as ``ogo`` (ogo.co.il), one host per country: a sitemap index lists every event page
as ``/<locale>/event/<country>/<city>/<yyyy>/<mm>/<dd>/<slug>-<id>``; only dates within ``days_ahead``
are fetched, in the configured locale, one request each, and each page's schema.org ``Event`` JSON-LD
is parsed by ``ogo.parse_event`` (Russian tags translated there, seller page fills a missing price or
picture). The venue's zone comes from the URL's city (``abroad.local_zone``); a date-only start is
midnight there. robots.txt says ``Disallow: /`` and the pages send ``noindex`` (Nov 2026): read anyway
by the owner's choice.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from typing import cast
from urllib.parse import urljoin

import httpx

from partylinks.models import ScrapedEvent
from partylinks.parsing import ParseError, zone
from partylinks.scrapers import ogo
from partylinks.scrapers.abroad import local_zone
from partylinks.scrapers.ogo import OgoScraper, OgoSettings
from partylinks.scrapers.seller_offers import SellerLookup

# https://us.ogo.events/he/event/us/new-york/2026/11/20/<slug>-<id>
_EVENT_URL = re.compile(
    r"^(https?://[^/]+)/[a-z]{2}(/event/([a-z]{2})/([^/]+)/(\d{4})/(\d{2})/(\d{2})/[^/?#]*-[A-Za-z0-9]+)/?$"
)


class OgoAbroadSettings(OgoSettings):
    country: str  # ISO code of the host's country, as in the event paths ("US", "DE")
    default_zone: str  # the zone of a city local_zone does not know


class _OgoAbroadScraper(OgoScraper):
    settings_model = OgoAbroadSettings
    abroad_only = True

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = cast(OgoAbroadSettings, self.settings)  # settings_model above; OgoScraper types it as OgoSettings
        today = datetime.now(zone(s.default_zone)).date()
        index = await self.get_text(s.site_url + s.sitemap_path)
        sitemaps = [xml for url in ogo._locs(index) if (xml := await self._sitemap(url)) is not None]
        urls = upcoming_event_urls(sitemaps, s.locale, today - timedelta(days=1), today + timedelta(days=s.days_ahead))
        if len(urls) > s.max_events:
            self.log.warning("listing truncated at max_events=%d (%d found)", s.max_events, len(urls))
        self._sellers = SellerLookup(self, s)
        async for event in self.stream_isolated(urls[: s.max_events], self._fetch_event, label=lambda u: u):
            yield event

    async def _fetch_event(self, url: str) -> ScrapedEvent | None:
        try:
            page = await self.get_text(url)
        except httpx.HTTPStatusError as exc:
            moved = ogo._NEXT_REDIRECT.search(exc.response.text) if exc.response.is_redirect else None
            if not moved:
                raise
            url = urljoin(url, moved.group(1))
            page = await self.get_text(url)
        event = parse_event(page, url, self.name, cast(OgoAbroadSettings, self.settings).default_zone)
        return await self._sellers.fill(event) if event is not None else None


class OgoUsScraper(_OgoAbroadScraper):
    name = "ogo_us"


class OgoDeScraper(_OgoAbroadScraper):
    name = "ogo_de"


def upcoming_event_urls(sitemaps: list[str], locale: str, first: date, last: date) -> list[str]:
    """Event pages dated ``first``..``last`` in ``locale``, each once, soonest first."""
    found: dict[str, date] = {}
    for url in (u for xml in sitemaps for u in ogo._locs(xml)):
        match = _EVENT_URL.match(url)
        if match and first <= (day := date(*map(int, match.group(5, 6, 7)))) <= last:
            found.setdefault(f"{match.group(1)}/{locale}{match.group(2)}", day)
    return sorted(found, key=found.__getitem__)


def parse_event(page_html: str, url: str, source: str, default_zone: str) -> ScrapedEvent | None:
    """The page's event via ``ogo.parse_event``, in the venue's country and zone; None when cancelled."""
    match = _EVENT_URL.match(url)
    if match is None:
        raise ParseError(f"{url}: not an OGO event page")
    country = match.group(3).upper()
    tz = local_zone(country, default_zone, match.group(4).replace("-", " "))
    # ogo.parse_event reads the city and id from an Israeli path: same layout, country segment swapped.
    event = ogo.parse_event(page_html, url.replace(f"/event/{match.group(3)}/", "/event/il/", 1), tz)
    if event is None:
        return None
    # Re-validated, not model_copy: the URL must become an HttpUrl again.
    return ScrapedEvent.model_validate(
        {**event.model_dump(), "source": source, "url": url, "country": country, "time_zone": tz}
    )
