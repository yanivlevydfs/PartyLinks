"""Eventati (eventati.org, إڤنتاتي) - a box office for culture in Arab society in Israel (Nazareth, Haifa,
Umm al-Fahm, Shefa-Amr, Jerusalem...): concerts, plays, children's shows, film screenings.

A Wix site whose events are Wix Events. Read, gently (the site answers a burst of requests with a
"Checking Your Request" page, 403): 1) ``/allevents``, the events widget, whose page carries every listed
upcoming event as JSON (``"events":[{...}]``, ``hasMore: false``: the whole list, ~10); 2) the event
pages of ``/event-pages-sitemap.xml`` changed in the last ``sitemap_days`` (an upcoming event can be
missing from the widget: a sold-out or unlisted copy, 27.11.2026 ``mhmd-n-mt-fy-sfya-2``) and every
event slug on the organisers' pages (``dynamic-organization`` sitemap). Each event is read from its
Hebrew page (``/he/event-details/<slug>``: the Hebrew title when the organiser wrote one), which embeds
the Wix event object (``"event":{...}``: schedule with time zone, venue with coordinates, town, price,
sold out, status) and the JSON-LD Event (its ticket tiers). No categories: genres come from the text,
and ``source_default_genres`` when it names none. A page that is only the challenge stops the run as
blocked; the source goes direct (``proxied: false``), never through another IP.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import clean_str, jsonld_nodes, parse_iso, schema_types, to_decimal, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.ticket4u import town_at

_LOC = re.compile(r"<loc>([^<]+)</loc>\s*(?:<lastmod>([^<]+)</lastmod>)?")
_EVENT_SLUG = re.compile(r"event-details/([a-z0-9][a-z0-9-]*)")
_PRICE = re.compile(r"(\d+(?:[.,]\d+)?)")
# Wix Events status: 0 scheduled, 1 started, 2 ended, 3 canceled.
_CANCELED = 3


class EventatiSettings(SourceSettings):
    site_url: str = "https://www.eventati.org"
    list_path: str = "/allevents"
    event_path: str = "/he/event-details/{slug}"
    event_sitemap: str = "/event-pages-sitemap.xml"
    organizer_sitemap: str = "/dynamic-organization_p_c4b5f417_3712_4370_8507_28c6aefd5ce9_0_5000-sitemap.xml"
    # Event pages of the sitemap changed within this many days are read (older ones are past events).
    sitemap_days: int = 365
    block_marker: str = "Checking Your Request"


class EventatiScraper(BaseScraper[EventatiSettings]):
    name = "eventati"
    settings_model = EventatiSettings

    def is_blocked(self, response: httpx.Response) -> bool:
        return response.status_code == 403 and self.settings.block_marker in response.text[:3000]

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        listed = list_slugs(await self.get_text(s.site_url + s.list_path))
        since = (datetime.now(UTC) - timedelta(days=s.sitemap_days)).date().isoformat()
        sitemap = await self.optional("event sitemap", self.get_text(s.site_url + s.event_sitemap)) or ""
        recent = sitemap_slugs(sitemap, since)
        organizers = await self.optional("organizer sitemap", self.get_text(s.site_url + s.organizer_sitemap)) or ""
        named: list[str] = []
        for page_url, _ in _LOC.findall(organizers):
            page = await self.optional(f"organizer {page_url}", self.get_text(page_url)) or ""
            named += _EVENT_SLUG.findall(page)
        slugs = list(dict.fromkeys([*listed, *named, *recent]))
        self.log.debug(
            "%d listed, %d on organiser pages, %d recent in the sitemap: %d pages",
            len(listed),
            len(named),
            len(recent),
            len(slugs),
        )
        retention = RetentionSettings()
        async for event in self.stream_isolated(slugs, self._event, label=lambda slug: f"event {slug}"):
            if event and is_current(
                event.start_at, event.end_at, retention, self.timezone, time_known=event.time_known
            ):
                yield event

    async def _event(self, slug: str) -> ScrapedEvent | None:
        url = self.settings.site_url + self.settings.event_path.format(slug=slug)
        return parse_event(await self.get_text(url), url, self.timezone)


def _objects(page_html: str, marker: str) -> Any:
    """The JSON value after the first ``marker`` in the page's Wix data. The marker ends with the value's
    opening (``"event":{"id"``): a page has other, empty ``"event":`` keys before the event's own
    (``/he/event-details/anhyar``, 27.11.2026: the first is not the event)."""
    start = page_html.find(marker)
    if start < 0:
        return None
    opening = start + marker.index(":") + 1
    try:
        value, _ = json.JSONDecoder().raw_decode(page_html, opening)
    except json.JSONDecodeError:
        return None
    return value


def list_slugs(page_html: str) -> list[str]:
    """The slugs of the events the ``/allevents`` widget lists."""
    rows = _objects(page_html, '"events":[{"id"')
    if not isinstance(rows, list):
        return list(dict.fromkeys(_EVENT_SLUG.findall(page_html)))
    return [str(r["slug"]) for r in rows if isinstance(r, dict) and r.get("slug")]


def sitemap_slugs(sitemap_xml: str, since: str) -> list[str]:
    """Event slugs of the sitemap whose page changed on or after ``since`` (YYYY-MM-DD)."""
    out = []
    for loc, lastmod in _LOC.findall(sitemap_xml):
        if (m := _EVENT_SLUG.search(loc)) and (not lastmod or lastmod[:10] >= since):
            out.append(m.group(1))
    return out


def _tiers(page_html: str) -> list[TicketTier]:
    tiers: list[TicketTier] = []
    for node in jsonld_nodes(page_html):
        if "offer" not in schema_types(node) or not node.get("name"):
            continue
        price = to_decimal(node.get("price"))
        tiers.append(
            TicketTier(
                name=str(node["name"]),
                price=price,
                currency="ILS",
                is_free=price == 0,
                is_sold_out="SoldOut" in str(node.get("availability") or ""),
            )
        )
    return tiers


def parse_event(page_html: str, url: str, timezone: str) -> ScrapedEvent | None:
    """One event page: the embedded Wix event object, its JSON-LD offers. None for a cancelled or
    undated event ("Date and time is TBD"), or a page without the event."""
    event = _objects(page_html, '"event":{"id"')
    if not isinstance(event, dict) or not event.get("id") or event.get("status") == _CANCELED:
        return None
    config = (event.get("scheduling") or {}).get("config") or {}
    if config.get("scheduleTbd") or not config.get("startDate"):
        return None
    tz = zone(config.get("timeZoneId") or timezone)
    start = parse_iso(config["startDate"])
    if start is None:
        return None
    start = start.astimezone(tz)
    end = parse_iso(config.get("endDate")) if not config.get("endDateHidden") else None
    location = event.get("location") or {}
    coordinates = location.get("coordinates") or {}
    full = location.get("fullAddress") or {}
    ld_place = next((n for n in jsonld_nodes(page_html) if "place" in schema_types(n)), {})
    ticketing = (event.get("registration") or {}).get("ticketing") or {}
    tiers = _tiers(page_html)
    prices = [t.price for t in tiers if t.price is not None]
    if not prices:  # "₪30": the widget's lowest / highest price
        for value in (ticketing.get("lowestPrice"), ticketing.get("highestPrice")):
            if (m := _PRICE.search(str(value or ""))) and (
                price := to_decimal(m.group(1).replace(",", "."))
            ) is not None:
                prices.append(price)
    image = ((event.get("mainImage") or {}).get("url")) or None
    return ScrapedEvent(
        source="eventati",
        source_event_id=str(event["id"]),
        url=url,
        title=clean_str(event.get("title")) or str(event.get("slug")),
        description=clean_str(event.get("description")),
        start_at=start,
        end_at=end.astimezone(tz) if end and end > start else None,
        time_known=True,
        venue_name=clean_str((location.get("name") or "").strip(" -")),
        address=clean_str(ld_place.get("address")) or clean_str(location.get("address")),
        # The town of the pin: the page writes it in Arabic ("سخنين", "أم الفحم،"), which names no town of ours.
        city=town_at(coordinates.get("lat"), coordinates.get("lng")) or clean_str(full.get("city")),
        latitude=coordinates.get("lat") if coordinates.get("lng") is not None else None,
        longitude=coordinates.get("lng") if coordinates.get("lat") is not None else None,
        images=[image] if image else [],
        tickets=tiers,
        min_price=min(prices) if prices else None,
        max_price=max(prices) if prices else None,
        is_sold_out=bool(ticketing.get("soldOut")),
        extra={"purchase_url": url},
    )
