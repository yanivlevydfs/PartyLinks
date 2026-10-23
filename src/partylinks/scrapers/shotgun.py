"""Shotgun (shotgun.live) - ticketing for electronic music: club nights, raves, festivals. Israel only.

Shotgun lists no Israeli events yet (October 2026: no Israeli city or venue country page, no
Israeli venue in its sitemaps). This source keeps watching at the cost of a few requests a run:
the Israel venue page (``/en/venues/-/israel``, a 404 today) and the city pages of
``city_paths``. A missing page is an empty listing, not a failure. Once they exist: venue pages
link their events; each event page's schema.org ``MusicEvent`` JSON-LD (venue, address with
country, coordinates, offers) is read by the same parser as Chillz, and only events whose
address is in Israel are kept. robots.txt allows everything.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Any

import httpx
from pydantic import Field

from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, jsonld_nodes, schema_types
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.chillz import EVENT_TYPES, parse_page

_LINK = r'href="(?:https://shotgun\.live)?(/(?:[a-z]{{2}}/)?{kind}/[a-z0-9][a-z0-9-]*)"'
_EVENT_LINK = re.compile(_LINK.format(kind="events"), re.IGNORECASE)
_VENUE_LINK = re.compile(_LINK.format(kind="venues"), re.IGNORECASE)
_ISRAEL = {"il", "isr", "israel", "ישראל"}


class ShotgunSettings(SourceSettings):
    site_url: str
    country_path: str  # venues of one country: /en/venues/-/israel
    city_paths: list[str] = Field(default_factory=list)
    max_events: int = Field(400, ge=1)


class ShotgunScraper(BaseScraper[ShotgunSettings]):
    name = "shotgun"
    settings_model = ShotgunSettings

    def is_blocked(self, response: httpx.Response) -> bool:
        return "vercel security checkpoint" in response.content[:200_000].decode("utf-8", errors="ignore").lower()

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        venues = links(await self._page(s.country_path), _VENUE_LINK, s.site_url)
        listings = [await self._page(p) for p in [*s.city_paths, *(v.removeprefix(s.site_url) for v in venues)]]
        urls = list(dict.fromkeys(u for page in listings for u in links(page, _EVENT_LINK, s.site_url)))
        self.log.info("listed %d Israeli venues, %d events", len(venues), len(urls))
        if len(urls) > s.max_events:
            self.log.warning("listing truncated at max_events=%d (%d found)", s.max_events, len(urls))
        async for event in self.stream_isolated(urls[: s.max_events], self._fetch_event, label=lambda u: u):
            yield event

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
    """The page's event when it takes place in Israel; None otherwise."""
    event = parse_page(page_html, url, timezone)
    if event is None or not _in_israel(page_html):
        return None
    return event.model_copy(update={"source": ShotgunScraper.name})


def _in_israel(page_html: str) -> bool:
    node = next((n for n in jsonld_nodes(page_html) if schema_types(n) & EVENT_TYPES), {})
    location: Any = node.get("location")
    location = location[0] if isinstance(location, list) and location else location
    address = location.get("address") if isinstance(location, dict) else None
    country = address.get("addressCountry") if isinstance(address, dict) else None
    country = country.get("name") if isinstance(country, dict) else country
    return (clean_str(country) or "").casefold() in _ISRAEL
