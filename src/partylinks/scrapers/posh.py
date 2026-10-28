"""Posh (posh.vip) - ticketing for parties and nightlife, almost all in the US (some Canada, Mexico).

Listing: the explore page's own JSON endpoint ``/api/bff/v1/explore/events`` with no location
(every upcoming event; ~1,600, 100 a page, ``nextCursor`` until null). It gives id, title, slug,
artwork, start / end, the venue's IANA zone, venue name, lowest price, organizer and lineup.
Details: each event page ``/e/<slug>`` is a Next.js page whose data rides in the RSC payload
(``self.__next_f.push`` strings): venue address, coordinates, description and currency. Times are
kept in the venue's own zone (the listing's ``timezone``), so New York events show New York time.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from pydantic import Field

from partylinks.countries import country_code
from partylinks.enrichment import UNKNOWN_COUNTRY
from partylinks.models import ScrapedEvent
from partylinks.parsing import ParseError, clean_str, parse_iso, to_decimal, to_float
from partylinks.scrapers.base import BaseScraper, SourceSettings

_PUSH = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)')
_US = {"usa", "us", "united states", "united states of america"}
# Posh has no categories: everything is a night out (the site's "nightclubs" category) unless the
# title says otherwise; these title words add the matching category word the enrichment knows.
# Music genres come from the title / description as for every source.
_KINDS = [
    (re.compile(r"halloween|thanksgiving|christmas|new year|nye\b|friendsgiving|valentine|cinco de mayo|juneteenth", re.I), "holidays"),
    (re.compile(r"\bmarket\b|\bvendors?\b|pop[- ]?up shop|\bbazaar\b|\bflea\b", re.I), "shopping"),
    (re.compile(r"\bbrunch|\bfood\b|tasting|\bwine\b|dinner", re.I), "food"),
    (re.compile(r"comedy|comedian|stand[- ]?up", re.I), "comedy"),
    (re.compile(r"happy hour", re.I), "happy hour"),
    (re.compile(r"\bfestival|\bfest\b", re.I), "festival"),
    (re.compile(r"\bconcert\b|\blive music\b|\bin concert\b", re.I), "concert"),
    (re.compile(r"karaoke", re.I), "karaoke"),
    (re.compile(r"\byoga\b|wellness|sound bath|meditation", re.I), "wellness"),
    (re.compile(r"networking|mixer|meet[- ]?up", re.I), "community"),
    (re.compile(r"tailgate|game day|\b5k\b|tournament", re.I), "sports"),
    (re.compile(r"\bpool party|pool day", re.I), "pool party"),
]
_NIGHT_OUT = {"holidays", "festival", "karaoke", "pool party", "happy hour"}  # still a party: keep nightclubs


def categories(title: str) -> list[str]:
    """Category words for the enrichment from a Posh title (no categories of its own)."""
    words = [word for pattern, word in _KINDS if pattern.search(title)]
    if not words or set(words) <= _NIGHT_OUT:
        words.insert(0, "nightclubs")
    return words


class PoshSettings(SourceSettings):
    site_url: str
    page_size: int = Field(100, ge=1, le=100)
    max_pages: int = Field(60, ge=1)


class PoshScraper(BaseScraper[PoshSettings]):
    name = "posh"
    settings_model = PoshSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        listed = await self._list()
        self.log.info("listed %d upcoming events", len(listed))
        async for event in self.stream_isolated(listed, self._fetch_event, label=lambda e: e.get("url", "")):
            yield event

    async def _list(self) -> list[dict[str, Any]]:
        s, events, cursor = self.settings, {}, None
        now = datetime.now(UTC)
        for _ in range(s.max_pages):
            params: dict[str, Any] = {"limit": s.page_size}
            if cursor is not None:
                params["cursor"] = cursor
            page = await self.get_json(s.site_url + "/api/bff/v1/explore/events", expect=dict, params=params)
            for e in page.get("events") or []:
                end = parse_iso(e.get("endDate") or e.get("startDate"))
                if e.get("id") and e.get("url") and (end is None or end > now):  # never past events
                    events[e["id"]] = e
            cursor = page.get("nextCursor")
            if cursor is None:
                return list(events.values())
        self.log.warning("listing truncated at max_pages=%d", s.max_pages)
        return list(events.values())

    async def _fetch_event(self, listed: dict[str, Any]) -> ScrapedEvent | None:
        url = f"{self.settings.site_url}/e/{listed['url']}"
        page = await self.optional(url, self.get_text(url))
        return parse_event(listed, rsc_text(page or ""), url)


def rsc_text(page_html: str) -> str:
    """The page's React Server Components payload, unescaped into one text."""
    parts = []
    for raw in _PUSH.findall(page_html):
        try:
            parts.append(json.loads(raw))
        except ValueError:
            continue
    return "".join(parts)


def _field(text: str, key: str) -> str | None:
    """The first JSON string value of ``"key":"..."`` in the payload."""
    m = re.search(r'"%s":("(?:[^"\\]|\\.)*")' % re.escape(key), text)
    if not m:
        return None
    try:
        value = json.loads(m.group(1))
    except ValueError:
        return None
    return None if value.startswith("$") else clean_str(value)  # "$undefined", "$1b:..." are references


def _country(address: str | None) -> str:
    """ISO country of the address's last part ("..., GA 30303, USA" -> US)."""
    last = (address or "").rsplit(",", 1)[-1].strip()
    if last.casefold() in _US:
        return "US"
    return country_code(last) if last else UNKNOWN_COUNTRY


def _city(address: str | None) -> str | None:
    """The city of "street, city, STATE zip, country"."""
    parts = [p.strip() for p in (address or "").split(",")]
    return clean_str(parts[-3]) if len(parts) >= 3 else None


def parse_event(listed: dict[str, Any], text: str, url: str) -> ScrapedEvent | None:
    """One event from its listing entry plus its page's payload (which may be empty)."""
    zone = clean_str(listed.get("timezone")) or "America/New_York"
    title, start = clean_str(listed.get("title")), parse_iso(listed.get("startDate"), zone)
    if not title or not start:
        raise ParseError(f"{listed.get('id')}: event without title or start")
    address = _field(text, "venueAddress")
    point = re.search(r'"location":\{"lat":(-?[\d.]+),"lng":(-?[\d.]+)\}', text)
    currency = re.search(r'"currency":"([A-Z]{3})","isAttendee"', text) or re.search(r'"currency":"([A-Z]{3})"', text)
    price = to_decimal(listed.get("minTicketPrice"))
    artists = [n for a in listed.get("lineup") or [] if (n := clean_str(a.get("name") if isinstance(a, dict) else a))]
    image = clean_str(listed.get("imageUrl"))
    return ScrapedEvent(
        source=PoshScraper.name,
        source_event_id=listed["id"],
        url=url,
        title=title,
        description=_field(text, "description"),
        start_at=start,
        end_at=parse_iso(listed.get("endDate"), zone),
        venue_name=clean_str(listed.get("venueName")),
        address=address,
        city=_city(address),
        latitude=to_float(point.group(1)) if point else None,
        longitude=to_float(point.group(2)) if point else None,
        country=_country(address),
        time_zone=zone,
        source_genres=categories(title),
        organizer=clean_str(listed.get("groupName")),
        artists=artists,
        image_url=image,
        images=[image] if image else [],
        currency=currency.group(1) if currency else "USD",
        min_price=price,
    )
