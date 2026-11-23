"""Kol Sadna (kolsadna.co.il, "כל סדנה") - a directory of ~1,400 workshops (art, cooking, meditation, dance,
tantra, sound healing...). Most are booked by groups on a date they pick (a lead form: birthdays, team days);
some also run dated sessions anyone can join, and those are the events read here.

WordPress + JetEngine. The wp-json ``course`` post type lists the workshops (``list_path``, ordered by last
change, only those changed in the last ``recent_days``: a workshop's page is edited when its sessions are set.
In a 274-page sample (Nov 2026) every one of the 22 workshops with open sessions had changed within 45 days,
and none of the older ones had any). The ``kind`` taxonomy names the site's categories (``kinds_path``).
The sessions are not in the REST rows: each workshop's page (one request each) carries them in its booking
script: ``a_partici_type`` ("can_join" / "both": open to anyone; "can_schedule": groups only),
``a_schedule.occurrences`` (date, start, end; ``canJoin``), ``a_location`` (the place as the host writes it:
a centre, a studio, or only the town), ``a_price``, ``a_virtual`` (online). The page's schema.org
``Service``/``Course`` gives the description, the category and the artwork; og:image as a fallback.

A session's place is ``a_location`` as venue and address (the enricher finds the town in it). When it is
only a town, the event keeps the town and no venue: the site names no hall, and a town's centre is no pin.
Online sessions are skipped (``skip_virtual``).
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from html import unescape
from typing import Any

from pydantic import Field

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import (
    clean_str,
    html_to_text,
    is_site_logo,
    jsonld_nodes,
    og_image,
    read_clock,
    schema_types,
    to_decimal,
    zone,
)
from partylinks.places import PlaceIndex, israeli_places
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings


class KolSadnaSettings(SourceSettings):
    site_url: str = "https://kolsadna.co.il"
    list_path: str = "/wp-json/wp/v2/course"
    list_fields: str = "id,link,title,modified,kind,location"
    kinds_path: str = "/wp-json/wp/v2/kind?per_page=100&_fields=id,name"
    # The workshop's areas (towns and regions it serves): a fallback town for a place written as a street only.
    locations_path: str = "/wp-json/wp/v2/location?per_page=100&_fields=id,name"
    per_page: int = 100
    max_pages: int = 20
    # Only workshops changed this recently are opened (see the module docstring).
    recent_days: int = Field(180, ge=1)
    # Booking modes whose sessions anyone can join.
    open_modes: list[str] = Field(default_factory=lambda: ["can_join", "both"])
    skip_virtual: bool = True
    # Extra names that are a whole town when they are the whole ``a_location`` (GeoNames knows the rest).
    towns: list[str] = Field(default_factory=list)
    # Town names in a place that GeoNames spells otherwise ("כרכור" -> "פרדס חנה-כרכור"), and landmarks.
    town_aliases: dict[str, str] = Field(default_factory=dict)
    # ``location`` terms that are regions, not towns ("מרכז", "צפון", "איזור השרון"...).
    regions: list[str] = Field(default_factory=list)
    ticket_name: str = "משתתף"
    every_session_genre: str = "סדנה"


class KolSadnaScraper(BaseScraper[KolSadnaSettings]):
    name = "kolsadna"
    settings_model = KolSadnaSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        since = (datetime.now(UTC) - timedelta(days=s.recent_days)).strftime("%Y-%m-%dT%H:%M:%S")

        async def page(index: int) -> tuple[list[dict[str, Any]], str | None]:
            params = {
                "per_page": s.per_page,
                "page": index + 1,
                "orderby": "modified",
                "order": "desc",
                "modified_after": since,
                "_fields": s.list_fields,
            }
            response = await self.request("GET", s.site_url + s.list_path, params=params)
            rows = response.json()
            return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else [], response.headers.get(
                "X-WP-Total"
            )

        rows = [r for r in await self.paginate(page, s.per_page, s.max_pages) if r.get("id") and r.get("link")]
        kinds = await self.optional("kinds", self.get_json(s.site_url + s.kinds_path, expect=list)) or []
        names = {k.get("id"): clean_str(unescape(str(k.get("name") or ""))) for k in kinds if isinstance(k, dict)}
        places = await self.optional("locations", self.get_json(s.site_url + s.locations_path, expect=list)) or []
        areas = {a.get("id"): clean_str(unescape(str(a.get("name") or ""))) for a in places if isinstance(a, dict)}
        self.log.debug("listed %d recently changed workshops", len(rows))

        async def sessions(row: dict[str, Any]) -> list[ScrapedEvent]:
            return parse_workshop(row, await self.get_text(str(row["link"])), names, s, self.timezone, areas)

        events: list[ScrapedEvent] = []
        async for batch in self.stream_isolated(rows, sessions, lambda r: f"workshop {r.get('id')}"):
            events.extend(e for e in batch if is_current(e.start_at, e.end_at, RetentionSettings(), self.timezone))
        for event in sorted(events, key=lambda e: e.start_at or datetime.max.replace(tzinfo=UTC)):
            yield event  # nearest first


def page_var(page_html: str, name: str) -> Any:
    """The JSON value of ``var <name> = ...;`` in the page's booking script; None when absent."""
    found = re.search(rf"\bvar {re.escape(name)} = ", page_html)
    if not found:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(page_html, found.end())
    except ValueError:
        return None
    return value


def parse_workshop(
    row: dict[str, Any],
    page_html: str,
    kinds: dict[Any, str | None],
    s: KolSadnaSettings,
    timezone: str,
    areas: dict[Any, str | None] | None = None,
) -> list[ScrapedEvent]:
    """One workshop's page -> one event per open, dated session (none for a group-only workshop)."""
    mode = page_var(page_html, "a_partici_type")
    schedule = page_var(page_html, "a_schedule")
    if mode not in s.open_modes or not isinstance(schedule, dict) or schedule.get("canJoin") is False:
        return []
    if s.skip_virtual and page_var(page_html, "a_virtual"):
        return []
    service = next((n for n in jsonld_nodes(page_html) if "course" in schema_types(n)), {})
    title = clean_str(unescape(str(page_var(page_html, "a_title") or service.get("name") or "")))
    title = title or clean_str(unescape(re.sub(r"<[^>]+>", " ", str((row.get("title") or {}).get("rendered") or ""))))
    if not title:
        return []
    place = clean_str(page_var(page_html, "a_location"))
    whole = bool(place) and _is_town(place or "", s)
    venue = None if not place or whole else place
    named = _named_town(venue, s) if venue else None
    # A place with no town in it ("ויתקין 13"): the workshop's only town, also for the geocoder.
    fallback = None if whole or named or not venue else _single_town(row, areas or {}, s)
    city = place if whole else (s.town_aliases.get(named or "") or fallback)
    address = f"{venue}, {fallback}" if venue and fallback else venue
    price = to_decimal(page_var(page_html, "a_price"))
    images = _images(page_html, service)
    genres = [s.every_session_genre, *(g for i in row.get("kind") or [] if (g := kinds.get(i)))]
    if category := clean_str(service.get("category")):
        genres.append(category)
    description = html_to_text(str(service.get("description") or "")) or None
    provider = service.get("provider") if isinstance(service.get("provider"), dict) else {}
    tz = zone(timezone)
    events: dict[str, ScrapedEvent] = {}
    for slot in schedule.get("occurrences") or []:
        if not isinstance(slot, dict):
            continue
        start, end = _times(slot, tz)
        if start is None:
            continue
        key = f"{row['id']}-{start:%Y%m%d%H%M}"
        events.setdefault(  # a session listed twice is one event
            key,
            ScrapedEvent(
                source=KolSadnaScraper.name,
                source_event_id=key,
                url=str(row["link"]),
                title=title,
                description=description,
                start_at=start,
                end_at=end,
                time_known=bool(slot.get("start")),
                venue_name=venue,
                address=address,
                city=city,
                source_genres=list(dict.fromkeys(g.lower() for g in genres if g)),
                images=images,
                tickets=[TicketTier(name=s.ticket_name, price=price)] if price else [],
                min_price=price if price else None,
                max_price=price if price else None,
                organizer=clean_str(unescape(str(provider.get("name") or ""))) or None,
            ),
        )
    return list(events.values())


def _times(slot: dict[str, Any], tz: Any) -> tuple[datetime | None, datetime | None]:
    """A session's start and end from {"date": "2026-12-18", "start": "09:30", "end": "16:30"}."""
    try:
        day = datetime.strptime(str(slot.get("date") or ""), "%Y-%m-%d")
    except ValueError:
        return None, None
    begin, finish = read_clock(slot.get("start")), read_clock(slot.get("end"))
    start = day.replace(hour=begin.hour if begin else 0, minute=begin.minute if begin else 0, tzinfo=tz)
    end = day.replace(hour=finish.hour, minute=finish.minute, tzinfo=tz) if finish else None
    return start, end if end and end > start else None


@lru_cache(maxsize=1)
def _towns() -> PlaceIndex:
    return PlaceIndex(list(israeli_places(0)))


def _is_town(place: str, s: KolSadnaSettings) -> bool:
    """A place that is only a town's name ("מודיעין"), not a venue in it."""
    text = place.strip(" .")
    return text in s.towns or _towns().is_place(text)


def _named_town(place: str, s: KolSadnaSettings) -> str | None:
    """The town a place names: an alias of the config ("כרכור") as written, or a GeoNames town."""
    alias = next((a for a in s.town_aliases if a in place), None)
    if alias:
        return alias
    hits = _towns().find(place)
    return max(hits)[1] if hits else None


def _single_town(row: dict[str, Any], areas: dict[Any, str | None], s: KolSadnaSettings) -> str | None:
    """The workshop's one town among its ``location`` terms (regions left out); None for none or several."""
    regions = {r.casefold() for r in s.regions}
    towns = {a for i in row.get("location") or [] if (a := areas.get(i)) and a.casefold() not in regions}
    return towns.pop() if len(towns) == 1 else None


def _images(page_html: str, service: dict[str, Any]) -> list[str]:
    value = service.get("image")
    urls = [str(u) for u in (value if isinstance(value, list) else [value]) if u]
    if image := page_var(page_html, "a_image"):
        urls.insert(0, str(image))
    if not urls and (og := og_image(page_html)):
        urls.append(og)
    return [u for u in dict.fromkeys(urls) if u.startswith(("http://", "https://")) and not is_site_logo(u)]
