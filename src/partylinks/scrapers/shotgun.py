"""Shotgun (shotgun.live) - ticketing for electronic music: club nights, raves, festivals.

Listing: every city page in Shotgun's cities sitemap (240, mostly France, Brazil and the US;
the first screen of each, ~14 events), plus the Israel venue page and the Israeli city pages of
``city_paths`` (404 in October 2026: Shotgun lists no Israeli events yet; a missing page is an
empty listing, not a failure). Details: each event page's schema.org ``MusicEvent`` JSON-LD
(venue, address with country, coordinates, offers), read by the same parser as Chillz. Events
abroad carry their country and go to the abroad section. robots.txt allows everything.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from typing import Any

import httpx
from pydantic import Field

from partylinks.enrichment import UNKNOWN_COUNTRY
from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, jsonld_nodes, schema_types
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.chillz import EVENT_TYPES, parse_page

_LINK = r'href="(?:https://shotgun\.live)?(/(?:[a-z]{{2}}/)?{kind}/[a-z0-9][a-z0-9-]*)"'
_EVENT_LINK = re.compile(_LINK.format(kind="events"), re.IGNORECASE)
_VENUE_LINK = re.compile(_LINK.format(kind="venues"), re.IGNORECASE)
_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_ISRAEL = {"il", "isr", "israel", "ישראל"}


class ShotgunSettings(SourceSettings):
    site_url: str
    country_path: str  # venues of one country: /en/venues/-/israel
    city_paths: list[str] = Field(default_factory=list)
    # Every city page listed here too (events abroad); None = Israel only.
    cities_sitemap_path: str | None = None
    max_events: int = Field(6000, ge=1)


class ShotgunScraper(BaseScraper[ShotgunSettings]):
    name = "shotgun"
    settings_model = ShotgunSettings

    def is_blocked(self, response: httpx.Response) -> bool:
        return "vercel security checkpoint" in response.content[:200_000].decode("utf-8", errors="ignore").lower()

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        venues = links(await self._page(s.country_path), _VENUE_LINK, s.site_url)
        cities = _LOC.findall(await self._page(s.cities_sitemap_path)) if s.cities_sitemap_path else []
        paths = [*s.city_paths, *(u.removeprefix(s.site_url) for u in [*venues, *cities] if u.startswith(s.site_url))]
        listings = await self.gather_pages(list(dict.fromkeys(paths)))
        urls = list(dict.fromkeys(u for page in listings for u in links(page, _EVENT_LINK, s.site_url)))
        self.log.info("listed %d Israeli venues, %d cities, %d events", len(venues), len(cities), len(urls))
        if len(urls) > s.max_events:
            self.log.warning("listing truncated at max_events=%d (%d found)", s.max_events, len(urls))
        async for event in self.stream_isolated(urls[: s.max_events], self._fetch_event, label=lambda u: u):
            yield event

    async def gather_pages(self, paths: list[str]) -> list[str]:
        """Listing pages in parallel (the client's concurrency); a failing page lists nothing."""
        pages = await asyncio.gather(*(self.optional(p, self._page(p)) for p in paths))
        return [p or "" for p in pages]

    async def _page(self, path: str) -> str:
        """A listing page; one that does not exist (yet) lists nothing."""
        try:
            return await self.get_text(self.settings.site_url + path)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return ""
            raise

    async def _fetch_event(self, url: str) -> ScrapedEvent | None:
        return parse_event(await self.get_text(url), url, self.timezone)


def links(page_html: str, pattern: re.Pattern[str], site_url: str) -> list[str]:
    """Absolute URLs of the links matching ``pattern``, in order, once each."""
    return list(dict.fromkeys(site_url + path for path in pattern.findall(page_html)))


def parse_event(page_html: str, url: str, timezone: str) -> ScrapedEvent | None:
    """The page's event; one abroad carries its ISO country (None = Israel)."""
    event = parse_page(page_html, url, timezone)
    if event is None:
        return None
    country = _country(page_html)
    abroad = None if country in _ISRAEL else (country.upper() if len(country) == 2 else UNKNOWN_COUNTRY)
    return event.model_copy(update={"source": ShotgunScraper.name, "country": abroad})


def _country(page_html: str) -> str:
    """The Event's addressCountry, lower case ("" when not given)."""
    node = next((n for n in jsonld_nodes(page_html) if schema_types(n) & EVENT_TYPES), {})
    location: Any = node.get("location")
    location = location[0] if isinstance(location, list) and location else location
    address = location.get("address") if isinstance(location, dict) else None
    country = address.get("addressCountry") if isinstance(address, dict) else None
    country = country.get("name") if isinstance(country, dict) else country
    return (clean_str(country) or "").casefold()
