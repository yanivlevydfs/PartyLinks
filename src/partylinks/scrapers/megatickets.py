"""Megatickets (megatickets.co.il).

robots.txt disallows ``/api/`` and ``/search``, so only public pages are read.
Listing: the ``/events`` page links every Israeli event as ``/events/<slug>``
(the site's ``/festivals`` are travel packages abroad and are not collected).
Details: each event page carries a schema.org ``MusicEvent`` JSON-LD block (dates,
venue, performers, ticket offers with availability) - parsed with the generic
schema.org parser shared with Chillz. The age limit only exists in the page's
React payload (``ageRestriction``) and is read from there.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

from pydantic import Field

from partylinks.models import ScrapedEvent
from partylinks.parsing import ParseError, clean_str, jsonld_nodes, schema_types, to_int
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.chillz import EVENT_TYPES, parse_page

# Inside the RSC payload the JSON is string-escaped: \"ageRestriction\":\"הכניסה מגיל 23+\"
_AGE = re.compile(r'ageRestriction\\?"\s*:\s*\\?"([^"\\]*)')
_NUMBER = re.compile(r"\d+")


class MegaticketsSettings(SourceSettings):
    site_url: str
    list_path: str
    event_page_path: str  # must contain "{slug}"
    max_events: int = Field(500, ge=1)


class MegaticketsScraper(BaseScraper[MegaticketsSettings]):
    name = "megatickets"
    settings_model = MegaticketsSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        slugs = await self._list_slugs()
        self.log.info("listed %d events", len(slugs))
        async for event in self.stream_isolated(slugs, self._fetch_event, label=lambda s: s):
            yield event

    async def _list_slugs(self) -> list[str]:
        s = self.settings
        prefix = re.escape(s.event_page_path.split("{slug}")[0])
        page = await self.get_text(s.site_url + s.list_path)
        slugs = list(dict.fromkeys(re.findall(rf'href="{prefix}([^"/?#]+)"', page)))
        if len(slugs) > s.max_events:
            self.log.warning("listing truncated at max_events=%d (%d found)", s.max_events, len(slugs))
        return slugs[: s.max_events]

    async def _fetch_event(self, slug: str) -> ScrapedEvent:
        url = self.settings.site_url + self.settings.event_page_path.format(slug=slug)
        return parse_event(await self.get_text(url), url, self.timezone)


def parse_event(page_html: str, url: str, default_tz: str) -> ScrapedEvent:
    event = parse_page(page_html, url, default_tz)
    if event is None:
        raise ParseError(f"{url}: no schema.org event on the page")
    node = next(n for n in jsonld_nodes(page_html) if schema_types(n) & EVENT_TYPES and n.get("name"))
    address = (node.get("location") or {}).get("address") or {}
    age_match = _AGE.search(page_html)
    age_text = clean_str(age_match.group(1)) if age_match else None
    age_number = _NUMBER.search(age_text or "")
    data = event.model_dump()
    data.update(
        source=MegaticketsScraper.name,
        # streetAddress already ends with the city.
        address=clean_str(address.get("streetAddress")) or event.address,
        city=clean_str(address.get("addressLocality")) or event.city,
        # /api/og/... is a generated share card on a robots-disallowed path, not artwork.
        images=[i for i in event.images if "/api/" not in i],
        image_url=None,
        min_age=to_int(age_number.group()) if age_number else None,
        extra={**event.extra, "age_restriction": age_text},
    )
    return ScrapedEvent.model_validate(data)  # re-validated so the artwork merge sees the filtered images
