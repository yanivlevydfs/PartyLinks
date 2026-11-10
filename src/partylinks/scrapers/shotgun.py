"""Shotgun (shotgun.live) - ticketing for electronic music: club nights, raves, festivals.

Listing: every city page in Shotgun's cities sitemap (240, mostly France, Brazil and the US;
the first screen of each, ~14 events), plus the Israel venue page and the Israeli city pages of
``city_paths`` (404 in October 2026: Shotgun lists no Israeli events yet; a missing page is an
empty listing, not a failure). Details: each event page's schema.org ``MusicEvent`` JSON-LD
(venue, address with country, coordinates, offers), read by the same parser as Chillz; while
sales run the JSON-LD often has ``"offers": null``, so the page's own ticket types (``"deals"``
in its RSC payload) give the tiers then. Events
abroad carry their country and go to the abroad section. robots.txt allows everything.

A city page shows only its first screen, so the newest ``event_sitemaps`` of the sitemap index's
event sitemaps (2,545 of 100 URLs each, in creation order) are read too: their pages last
modified within ``sitemap_max_age_days`` (older ones are nearly all past), in the countries the
city pages gave events in; past ones dropped. Vercel answers too many requests with a 429
checkpoint: retried with backoff (on the next proxy while the pool lasts), and one still 429
stops the run cleanly as blocked, with what it had.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from itertools import batched
from typing import Any

import httpx
from pydantic import Field

from partylinks.config import RetentionSettings
from partylinks.enrichment import UNKNOWN_COUNTRY
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import clean_str, jsonld_nodes, parse_iso, schema_types, to_decimal
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceBlockedError, SourceSettings, gather_or_cancel
from partylinks.scrapers.chillz import EVENT_TYPES, parse_page
from partylinks.scrapers.posh import rsc_text

_LINK = r'href="(?:https://shotgun\.live)?(/(?:[a-z]{{2}}/)?{kind}/[a-z0-9][a-z0-9-]*)"'
_EVENT_LINK = re.compile(_LINK.format(kind="events"), re.IGNORECASE)
_VENUE_LINK = re.compile(_LINK.format(kind="venues"), re.IGNORECASE)
_DEALS = '"deals":'
_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_EVENT_SITEMAP = re.compile(r"<loc>\s*https?://[^<\s/]+(/[^<\s]*/events/sitemap/(\d+)\.xml)\s*</loc>")
_LASTMOD = re.compile(r"<lastmod>\s*([^<\s]+)\s*</lastmod>")
_ISRAEL = {"il", "isr", "israel", "ישראל"}


class ShotgunSettings(SourceSettings):
    site_url: str
    country_path: str  # venues of one country: /en/venues/-/israel
    city_paths: list[str] = Field(default_factory=list)
    # Every city page listed here too (events abroad); None = Israel only.
    cities_sitemap_path: str | None = None
    max_events: int = Field(6000, ge=1)
    # The index of every sitemap; its newest `event_sitemaps` event sitemaps add the events the
    # city pages' first screen misses. None or 0 = city pages only.
    sitemap_index_path: str | None = "/sitemap.xml"
    event_sitemaps: int = Field(300, ge=0)
    # Sitemap pages last modified longer ago are skipped: nearly all past.
    sitemap_max_age_days: int = Field(14, ge=1)
    # Event sitemaps read before their event pages are fetched.
    sitemaps_per_batch: int = Field(20, ge=1)


class ShotgunScraper(BaseScraper[ShotgunSettings]):
    name = "shotgun"
    settings_model = ShotgunSettings

    def is_blocked(self, response: httpx.Response) -> bool:
        if response.status_code == 429:  # the checkpoint for too many requests: backoff, not blocked yet
            return False
        return "vercel security checkpoint" in response.content[:200_000].decode("utf-8", errors="ignore").lower()

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        venues = links(await self._page(s.country_path), _VENUE_LINK, s.site_url)
        cities = _LOC.findall(await self._page(s.cities_sitemap_path)) if s.cities_sitemap_path else []
        paths = [*s.city_paths, *(u.removeprefix(s.site_url) for u in [*venues, *cities] if u.startswith(s.site_url))]
        listings = await self.gather_pages(list(dict.fromkeys(paths)))
        urls = list(dict.fromkeys(u for page in listings for u in links(page, _EVENT_LINK, s.site_url)))
        self.log.debug("listed %d Israeli venues, %d cities, %d events", len(venues), len(cities), len(urls))
        if len(urls) > s.max_events:
            self.log.warning("listing truncated at max_events=%d (%d found)", s.max_events, len(urls))
        countries: set[str | None] = set()  # None = Israel
        retention = RetentionSettings()
        async for event in self.stream_isolated(urls[: s.max_events], self._fetch_event, label=lambda u: u):
            countries.add(event.country)
            if is_current(event.start_at, event.end_at, retention, self.timezone, time_known=event.time_known):
                yield event  # never past events (a weekend pass that began two days ago included)
        if not countries:
            return  # no country to keep sitemap events in
        if not s.sitemap_index_path or not s.event_sitemaps:
            return
        listed = {_slug(u) for u in urls}
        maps = sorted(_EVENT_SITEMAP.findall(await self._page(s.sitemap_index_path)), key=lambda m: -int(m[1]))
        since = datetime.now(UTC) - timedelta(days=s.sitemap_max_age_days)
        # A slice of sitemaps, then its event pages: events keep coming, so the stall limit never
        # closes a run that is reading 300 sitemaps that yield no event themselves.
        for chunk in batched([path for path, _ in maps[: s.event_sitemaps]], s.sitemaps_per_batch):
            pages = await self.gather_pages(list(chunk))
            more = [u for xml in pages for u in sitemap_urls(xml, since) if _slug(u) not in listed]
            listed.update(_slug(u) for u in more)
            async for event in self.stream_isolated(more, self._fetch_event, label=lambda u: u):
                current = is_current(
                    event.start_at, event.end_at, retention, self.timezone, time_known=event.time_known
                )
                if event.country in countries and current:
                    yield event

    async def gather_pages(self, paths: list[str]) -> list[str]:
        """Listing pages in parallel (the client's concurrency); a failing page lists nothing."""
        pages = await gather_or_cancel(self.optional(p, self._page(p)) for p in paths)
        return [p or "" for p in pages]

    async def _page(self, path: str) -> str:
        """A listing page; one that does not exist (yet) lists nothing."""
        try:
            return await self._get(self.settings.site_url + path)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return ""
            raise

    async def _fetch_event(self, url: str) -> ScrapedEvent | None:
        return parse_event(await self._get(url), url, self.timezone)

    async def _get(self, url: str) -> str:
        """A page; still 429 after the backoff retries: the run stops (blocked), the parallel
        requests cancelled, instead of every page left failing alike."""
        try:
            return await self.get_text(url)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                raise SourceBlockedError(f"{self.name}: rate limited (429) after backoff") from exc
            raise


def links(page_html: str, pattern: re.Pattern[str], site_url: str) -> list[str]:
    """Absolute URLs of the links matching ``pattern``, in order, once each."""
    return list(dict.fromkeys(site_url + path for path in pattern.findall(page_html)))


def sitemap_urls(xml: str, since: datetime) -> list[str]:
    """Event pages of one event sitemap last modified at or after ``since`` (a bare date is UTC)."""
    urls = []
    for block in xml.split("</url>"):
        loc, lastmod = _LOC.search(block), _LASTMOD.search(block)
        when = parse_iso(lastmod.group(1)) if lastmod else None
        if loc and when is not None and when >= since:
            urls.append(loc.group(1))
    return urls


def _slug(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def parse_event(page_html: str, url: str, timezone: str) -> ScrapedEvent | None:
    """The page's event; one abroad carries its ISO country (None = Israel)."""
    event = parse_page(page_html, url, timezone)
    if event is None:
        return None
    country = _country(page_html)
    abroad = None if country in _ISRAEL else (country.upper() if len(country) == 2 else UNKNOWN_COUNTRY)
    update: dict[str, Any] = {"source": ShotgunScraper.name, "country": abroad}
    if not event.tickets and event.min_price is None and (tiers := page_deals(page_html)):
        update |= {"tickets": tiers, "currency": tiers[0].currency}
    return event.model_copy(update=update)


def page_deals(page_html: str) -> list[TicketTier]:
    """The page's own ticket types (``"deals"`` in its RSC payload: title, price), for events whose JSON-LD
    has no offers ("offers": null while sales run). Currency: the event's ("eur")."""
    text = rsc_text(page_html)
    at = text.find(_DEALS)
    try:
        deals = json.JSONDecoder().raw_decode(text, at + len(_DEALS))[0] if at >= 0 else []
    except ValueError:
        return []
    currency = re.search(r'"currency":"([a-z]{3})"', text)
    return [
        TicketTier(
            name=clean_str(d.get("title")) or "Ticket",
            price=price,
            currency=currency.group(1).upper() if currency else "EUR",
            is_free=price == 0,
        )
        for d in deals if isinstance(d, dict) and (price := to_decimal(d.get("price"))) is not None
    ]  # fmt: skip


def _country(page_html: str) -> str:
    """The Event's addressCountry, lower case ("" when not given)."""
    node = next((n for n in jsonld_nodes(page_html) if schema_types(n) & EVENT_TYPES), {})
    location: Any = node.get("location")
    location = location[0] if isinstance(location, list) and location else location
    address = location.get("address") if isinstance(location, dict) else None
    country = address.get("addressCountry") if isinstance(address, dict) else None
    country = country.get("name") if isinstance(country, dict) else country
    return (clean_str(country) or "").casefold()
