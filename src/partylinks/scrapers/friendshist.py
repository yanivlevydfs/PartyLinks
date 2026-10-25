"""FRIENDS (friends-hist.co.il) - the Histadrut's culture club: shows, plays, stand-up and kids'
shows in halls all over the country, subsidised for Histadrut members.

The WordPress site has no shows in its REST API (no ``show`` post type there, no Events
Calendar, no event JSON-LD); tickets are sold on its Smarticket tenant
``tickets.friends-hist.co.il``. So two reads per run:

* ``GET <tickets host>/api/shows`` (~3 MB): every show with every performance, hall
  (``event_place``), price list and availability - parsed by the smarticket module.
  ``/event/<id>`` is the ticket page.
* ``/hall-sitemap.xml`` (~180 halls), then each hall page's schema.org ``EventVenue``
  JSON-LD: name, street address and city (``addressLocality``). The API's hall name
  (``event_place``) is matched to it by normalised name; ~95% of performances get a city.
  (The "all shows" page and its admin-ajax "load more" pages also show cities, but
  admin-ajax answers 403 to server and proxy IPs after a page or two, Oct 2026.)

robots.txt allows everything. The Smarticket tenant is also listed under ``smarticket``.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str
from partylinks.scrapers import smarticket
from partylinks.scrapers.base import BaseScraper, SourceSettings, gather_or_cancel

type Hall = tuple[str | None, str | None]  # (city, street address)
_NOT_WORD = re.compile(r"\W+")
_LOC = re.compile(r"<loc>([^<]+)</loc>")
_LD_JSON = re.compile(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", re.DOTALL)


class FriendsHistSettings(SourceSettings):
    site_url: str
    hall_sitemap_path: str
    tickets_host: str
    shows_path: str
    event_page_path: str
    uploads_path: str
    # Organizer, and the venue of a performance with no hall.
    venue_name: str


class FriendsHistScraper(BaseScraper[FriendsHistSettings]):
    name = "friendshist"
    settings_model = FriendsHistSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        shows = smarticket.parse_shows_body(
            await self.get_text(f"https://{s.tickets_host}{s.shows_path}"), s.tickets_host
        )
        halls = await self.optional("halls", self._halls()) or {}
        now = datetime.now(ZoneInfo(self.timezone))
        events = [
            e
            for e in build_events(shows, halls, s, self.timezone, self.record_failure)
            if (e.end_at or e.start_at or now) >= now
        ]
        self.log.info("%d upcoming events, %d with a city", len(events), sum(1 for e in events if e.city))
        for event in events:
            yield event

    async def _halls(self) -> dict[str, Hall]:
        s = self.settings
        urls = _LOC.findall(await self.get_text(s.site_url + s.hall_sitemap_path))

        async def hall(url: str) -> tuple[str, Hall] | None:
            body = await self.optional(f"hall {url}", self.get_text(url))
            return parse_hall(body) if body else None

        halls = dict(h for h in await gather_or_cancel(hall(u) for u in urls) if h)
        self.log.info("halls: %d pages, %d with a city", len(urls), sum(1 for c, _ in halls.values() if c))
        return halls


def norm_title(title: str) -> str:
    return _NOT_WORD.sub("", html.unescape(title)).casefold()


def parse_hall(page_html: str) -> tuple[str, Hall] | None:
    """A hall page's EventVenue JSON-LD: (normalised name, (city, street address))."""
    for block in _LD_JSON.findall(page_html):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for node in data.get("@graph", [data]) if isinstance(data, dict) else []:
            if isinstance(node, dict) and node.get("@type") == "EventVenue" and node.get("name"):
                address = node.get("address") if isinstance(node.get("address"), dict) else {}
                return norm_title(node["name"]), (
                    clean_str(address.get("addressLocality")),
                    clean_str(address.get("streetAddress")),
                )
    return None


def build_events(
    shows: list[dict[str, Any]],
    halls: dict[str, Hall],
    s: FriendsHistSettings,
    timezone: str,
    on_error: Callable[[str, Exception], None] = lambda what, exc: None,
) -> list[ScrapedEvent]:
    """API performances as events, with city / address from their hall's page."""
    tenant = smarticket.SmarticketTenant(host=s.tickets_host, venue_name=s.venue_name)
    st = smarticket.SmarticketSettings(
        cron=s.cron,
        tenant_domain=s.tickets_host,
        shows_path=s.shows_path,
        event_page_path=s.event_page_path,
        uploads_path=s.uploads_path,
        calendar_path="",
        tenants=[tenant],
    )
    events = []
    for show, performance in smarticket.iter_performances(shows):
        try:
            event = smarticket.parse_event(show, performance, tenant, st, timezone)
        except Exception as exc:  # noqa: BLE001 - one bad performance never drops the rest
            on_error(f"performance {performance.get('id')}", exc)
            continue
        if event is None or event.start_at is None:
            continue
        city, address = halls.get(norm_title(str(event.extra.get("hall") or "")), (None, None))
        events.append(
            event.model_copy(
                update={
                    "source": FriendsHistScraper.name,
                    "source_event_id": event.source_event_id.rsplit(":", 1)[-1],
                    "city": city,
                    "address": address,
                    "organizer": s.venue_name,
                    "extra": {k: v for k, v in event.extra.items() if k != "tenant"},
                }
            )
        )
    return events
