"""OneCity municipal sites (WordPress + GeoDirectory: karmiel.muni.il, even-yehuda.muni.il, ...).

The city's events are GeoDirectory ``gd_event`` posts, served by the public REST API
``/wp-json/geodir/v2/events`` (only current ones: an event leaves when it ends) with the
title, content, featured image, categories and tags (the site's own: "הגיל הרך", "חינמי"...),
start / end date-time and the site's custom fields: the place (``gd_event_mikum``, or the
location fields ``street`` / ``city``), the organiser (``gd_event_irgun``) and the ticket or
registration link (``gd_event_buyticket`` / ``gd_evevnt_rishum``, kept as
``extra["purchase_url"]``; chat-group links are not a seller). A recurring event comes once per
date; it is kept once, at its next date. An event sold by a Smarticket box office the smarticket
source already reads (its link, or one in its text) is skipped. Mostly free community events.
robots.txt allows everything but WPForms uploads.
"""

from __future__ import annotations

import html
import re
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import clean_str, html_to_text, parse_iso, to_int
from partylinks.scrapers.base import BaseScraper, SourceBlockedError, SourceSettings
from partylinks.scrapers.matnasnet import covered_smarticket_hosts

_LINK_FIELDS = ("gd_event_buyticket", "gd_evevnt_rishum")  # sic: the site's own field name
_NOT_SELLERS = ("wa.me", "whatsapp.com", "facebook.com", "instagram.com")
_HREF = re.compile(r"https?://[^\s\"'<>]+")
_FREE_TAG = "חינמי"


class OneCitySite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str  # https://www.karmiel.muni.il  (the API is <url>/wp-json/geodir/v2/events)
    city: str


class OneCitySettings(SourceSettings):
    sites: list[OneCitySite] = Field(min_length=1)
    page_size: int = Field(default=100, ge=1, le=100)
    max_pages: int = Field(default=10, ge=1)


class OneCityScraper(BaseScraper[OneCitySettings]):
    name = "onecity"
    settings_model = OneCitySettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        skip = covered_smarticket_hosts()
        for site in self.settings.sites:
            try:
                rows = await self.optional(f"{site.url} events", self._rows(site))
            except SourceBlockedError as exc:  # one site's firewall never stops the others
                self.record_failure(site.url, exc)
                continue
            events = parse_rows(rows or [], site, self.timezone, skip)
            self.log.info("%s: %d events", site.city, len(events))
            for event in events:
                yield event

    async def _rows(self, site: OneCitySite) -> list[dict[str, Any]]:
        size = self.settings.page_size

        async def fetch_page(index: int) -> tuple[list[dict[str, Any]], str | None]:
            response = await self.request(
                "GET", f"{site.url}/wp-json/geodir/v2/events", params={"per_page": size, "page": index + 1}
            )
            rows = response.json()
            return (rows if isinstance(rows, list) else []), response.headers.get("X-WP-Total")

        return await self.paginate(fetch_page, size, self.settings.max_pages)


def _raw(row: dict[str, Any], key: str) -> str | None:
    """A field given as {"raw": ..., "rendered": ...} or as a plain string."""
    value = row.get(key)
    return clean_str((value.get("raw") or value.get("rendered")) if isinstance(value, dict) else value)


def _split_place(place: str | None) -> tuple[str | None, str | None]:
    """Venue and address of "מרכז צעירים, אילנות 7, כרמיאל"."""
    if not place:
        return None, None
    venue, *address = re.split(r",\s+|\s+-\s+", place, maxsplit=1)
    return clean_str(venue), clean_str(address[0]) if address else None


def parse_rows(
    rows: list[dict[str, Any]], site: OneCitySite, timezone: str, skip_hosts: frozenset[str]
) -> list[ScrapedEvent]:
    """One event per post (a recurring one at its first listed date, the API's next one)."""
    events: dict[str, ScrapedEvent] = {}
    for row in rows:
        post_id = str(to_int(row.get("id")) or "")
        if post_id and post_id not in events and (event := build_event(row, site, timezone, skip_hosts)):
            events[post_id] = event
    return list(events.values())


def build_event(
    row: dict[str, Any], site: OneCitySite, timezone: str, skip_hosts: frozenset[str]
) -> ScrapedEvent | None:
    """None for a post without a start (the theme's sample posts) or sold by a covered Smarticket box office."""
    title = html.unescape(_raw(row, "title") or "")
    start = parse_iso(_raw(row, "start_datetime"), timezone)
    link = clean_str(row.get("link"))
    if not title or not start or not link:
        return None
    content = _raw(row, "content") or ""
    links = [u for u in (clean_str(row.get(f)) for f in _LINK_FIELDS) if u and u.startswith("http")]
    if any(urlsplit(u).hostname in skip_hosts for u in [*links, *_HREF.findall(content)]):
        return None
    sellers = [u for u in links if not any(n in (urlsplit(u).hostname or "") for n in _NOT_SELLERS)]
    end = parse_iso(_raw(row, "end_datetime"), timezone)
    if row.get("all_day") in ("1", 1, True):
        end = None
    place = clean_str(row.get("gd_event_mikum"))
    venue, address = _split_place(place) if place else (None, clean_str(row.get("street")))
    categories = [c.get("name") for c in [*(row.get("post_category") or []), *(row.get("post_tags") or [])]]
    genres = list(dict.fromkeys(html.unescape(c) for c in categories if isinstance(c, str) and c.strip()))
    image = row.get("featured_image") if isinstance(row.get("featured_image"), dict) else {}
    host = (urlsplit(site.url).hostname or "").removeprefix("www.").split(".")[0]  # karmiel
    return ScrapedEvent(
        source=OneCityScraper.name,
        source_event_id=f"{host}:{row['id']}",
        url=link,
        title=title,
        description=html_to_text(content),
        start_at=start,
        end_at=end if end and end.date() == start.date() else None,  # a course's last date is not its end
        venue_name=venue,
        address=address,
        city=site.city,
        source_genres=genres,
        organizer=clean_str(row.get("gd_event_irgun")),
        image_url=clean_str(image.get("src")),
        tickets=[TicketTier(name="כניסה חופשית", is_free=True, price=0)] if _FREE_TAG in genres else [],
        extra={"purchase_url": sellers[0]} if sellers else {},
    )
