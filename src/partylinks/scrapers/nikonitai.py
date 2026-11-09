"""Niko Nitai Theatre (nikonitai.smarticket.co.il) - a fringe theatre hall in Jaffa: plays, festivals, talks.

Its Smarticket tenant answers ``/api/shows`` and the calendar feed with ``[]``, so the generic Smarticket
source finds nothing (the same case as ``beershevatheatre``, whose JSON-LD parser this reuses). Listing:
the tenant's home page links every bookable performance as ``<slug>/?id=<id>`` (~35, upcoming only; ids
missing from it answer without an Event, old ones the header links 404). Details: each ``/event/<id>`` page has a schema.org ``Event``
with name, start/end, address, one price, availability and the artwork. No category: genres come from
the theatre itself and the title / text. No robots.txt on tenant hosts.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

import httpx

from partylinks.models import ScrapedEvent
from partylinks.parsing import jsonld_nodes, schema_types
from partylinks.retention import RetentionSettings, is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.beershevatheatre import parse_performance

_PERFORMANCE = re.compile(r'href="[^"]*\?id=(\d+)"')


class NikoNitaiSettings(SourceSettings):
    tenant_url: str  # https://nikonitai.smarticket.co.il
    venue_name: str
    address: str
    city: str
    latitude: float
    longitude: float


class NikoNitaiScraper(BaseScraper[NikoNitaiSettings]):
    name = "nikonitai"
    settings_model = NikoNitaiSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        ids = performance_ids(await self.get_text(self.settings.tenant_url + "/"))
        self.log.debug("listed %d performances", len(ids))
        retention = RetentionSettings()
        async for event in self.stream_isolated(ids, self._fetch, label=lambda i: i):
            if event is not None and is_current(
                event.start_at, event.end_at, retention, self.timezone, time_known=event.time_known
            ):
                yield event

    async def _fetch(self, performance_id: str) -> ScrapedEvent | None:
        try:
            page = await self.get_text(f"{self.settings.tenant_url}/event/{performance_id}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:  # an old show the header still links (?id=307): gone
                return None
            raise
        return parse(page, performance_id, self.settings, self.timezone)


def performance_ids(page_html: str) -> list[str]:
    return list(dict.fromkeys(_PERFORMANCE.findall(page_html)))


def parse(page_html: str, performance_id: str, s: NikoNitaiSettings, timezone: str) -> ScrapedEvent | None:
    """One performance; None for a page with no Event (a closed or unlisted id) or a cancelled one."""
    if not any("event" in schema_types(n) for n in jsonld_nodes(page_html)):
        return None
    event = parse_performance(page_html, performance_id, s.tenant_url, timezone)
    if event is None:
        return None
    return event.model_copy(update={
        "source": NikoNitaiScraper.name,
        "venue_name": s.venue_name,
        "address": s.address,
        "city": s.city,
        "latitude": s.latitude,
        "longitude": s.longitude,
        "organizer": s.venue_name,
        "event_types": ["theatre"],
        "extra": {},
    })  # fmt: skip
