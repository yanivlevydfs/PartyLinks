"""Posh (posh.vip) - ticketing for parties and nightlife, almost all in the US (some Canada, Mexico).

Listing: the explore page's own JSON endpoint ``/api/bff/v1/explore/events`` with no location
(every upcoming event; ~1,600, 100 a page, ``nextCursor`` until null). It gives id, title, slug,
artwork, start / end, the venue's IANA zone, venue name, lowest price, organizer and lineup.
Details: each event page ``/e/<slug>`` is a Next.js page whose data rides in the RSC payload
(``self.__next_f.push`` strings): venue address, coordinates, description and currency. Times are
kept in the venue's own zone (the listing's ``timezone``), so New York events show New York time.

The endpoint gives only events starting today plus running multi-day ones (~1,600, ~230 today,
none later; ``when`` / date / city params are ignored). So every page of the recent events
sitemap (``/sitemap-events-recent.xml``, 50k URLs) whose ``<lastmod>`` is within
``sitemap_max_age_days`` is read too (~24k: those with a lastmod are upcoming, those without are
past), each page giving its own listing fields in the payload's ``eventData``; past ones dropped.
An event listed without a lowest price (RSVP events, most of them free) gets its tiers from the event
page's own tRPC call ``events.getEventTickets`` (name, price, price with fees).
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import Field

from partylinks.config import RetentionSettings
from partylinks.countries import country_code
from partylinks.enrichment import UNKNOWN_COUNTRY
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import ParseError, clean_str, parse_iso, to_decimal, to_float
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_PUSH = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)')
_SITEMAP_URL = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>\s*<lastmod>\s*([^<\s]+)\s*</lastmod>")
_EVENT_DATA = '"eventData":'
_US = {"usa", "us", "united states", "united states of america"}
# Posh has no categories: everything is a night out (the site's "nightclubs" category) unless the
# title says otherwise; these title words add the matching category word the enrichment knows.
# Music genres come from the title / description as for every source.
_KINDS = [
    (
        re.compile(
            r"halloween|thanksgiving|christmas|new year|nye\b|friendsgiving|valentine|cinco de mayo|juneteenth",
            re.IGNORECASE,
        ),
        "holidays",
    ),
    (re.compile(r"\bmarket\b|\bvendors?\b|pop[- ]?up shop|\bbazaar\b|\bflea\b", re.IGNORECASE), "shopping"),
    (re.compile(r"\bbrunch|\bfood\b|tasting|\bwine\b|dinner", re.IGNORECASE), "food"),
    (re.compile(r"comedy|comedian|stand[- ]?up", re.IGNORECASE), "comedy"),
    (re.compile(r"happy hour", re.IGNORECASE), "happy hour"),
    (re.compile(r"\bfestival|\bfest\b", re.IGNORECASE), "festival"),
    (re.compile(r"\bconcert\b|\blive music\b|\bin concert\b", re.IGNORECASE), "concert"),
    (re.compile(r"karaoke", re.IGNORECASE), "karaoke"),
    (re.compile(r"\byoga\b|wellness|sound bath|meditation", re.IGNORECASE), "wellness"),
    (re.compile(r"networking|mixer|meet[- ]?up", re.IGNORECASE), "community"),
    (re.compile(r"tailgate|game day|\b5k\b|tournament", re.IGNORECASE), "sports"),
    (re.compile(r"\bpool party|pool day", re.IGNORECASE), "pool party"),
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
    # Upcoming events beyond today's listing; None = the listing alone.
    sitemap_path: str | None = "/sitemap-events-recent.xml"
    # Sitemap pages last modified longer ago are skipped (no lastmod = a past event, skipped too).
    sitemap_max_age_days: int = Field(60, ge=1)
    # The event page's tRPC call for its tickets (input: {"eventId": ...}), for events listed without a price.
    tickets_path: str = "/api/web/v2/trpc/events.getEventTickets"


class PoshScraper(BaseScraper[PoshSettings]):
    name = "posh"
    settings_model = PoshSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        listed = await self._list()
        self.log.debug("listed %d upcoming events", len(listed))
        retention = RetentionSettings()
        async for event in self.stream_isolated(listed, self._fetch_event, label=lambda e: e.get("url", "")):
            if is_current(event.start_at, event.end_at, retention, self.timezone):  # a multi-day one past its window
                yield event
        s = self.settings
        if not s.sitemap_path:
            return
        xml = await self.get_text(s.site_url + s.sitemap_path)
        since = datetime.now(UTC) - timedelta(days=s.sitemap_max_age_days)
        known = {f"{s.site_url}/e/{e['url']}" for e in listed}
        urls = [u for u in sitemap_urls(xml, since) if u not in known]
        self.log.debug("sitemap: %d more upcoming event pages", len(urls))
        async for event in self.stream_isolated(urls, self._fetch_page, label=lambda u: u):
            if is_current(event.start_at, event.end_at, retention, self.timezone):  # never past events
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
        return await self._priced(parse_event(listed, rsc_text(page or ""), url))

    async def _fetch_page(self, url: str) -> ScrapedEvent | None:
        text = rsc_text(await self.get_text(url))
        listed = page_listing(text)
        # None: a not-found page sent as 200 (deleted)
        return await self._priced(parse_event(listed, text, url)) if listed else None

    async def _priced(self, event: ScrapedEvent | None) -> ScrapedEvent | None:
        """No lowest price in the listing (RSVP events, hidden prices): the event page's own ticket call."""
        if event is None or event.min_price is not None:
            return event
        s = self.settings
        params = {"input": json.dumps({"eventId": event.source_event_id})}
        call = self.get_json(s.site_url + s.tickets_path, params=params, expect=dict)
        data = await self.optional(f"{event.url} tickets", call)
        event.tickets = parse_tickets(data or {}, event.currency)
        return event


_NAMED_PRICE = re.compile(r"\$\s*(\d+(?:\.\d{1,2})?)")


def parse_tickets(data: dict[str, Any], currency: str) -> list[TicketTier]:
    """Tiers of ``events.getEventTickets``: what a buyer pays (``totalPrice``, fees included, as the
    listing's ``minPriceWithFees``). A 0 ticket named "$15 General Admission" is paid at the door."""
    groups = ((data.get("result") or {}).get("data") or {}).get("ticketGroups") or []
    tiers = []
    for ticket in (t for g in groups if isinstance(g, dict) for t in g.get("tickets") or [] if isinstance(t, dict)):
        name = clean_str(ticket.get("name")) or "Ticket"
        price = to_decimal(ticket.get("totalPrice") if ticket.get("totalPrice") is not None else ticket.get("price"))
        if ticket.get("isHidden") or ticket.get("priceHidden") or price is None:
            continue
        named = _NAMED_PRICE.search(name) if not price else None
        tiers.append(
            TicketTier(
                name=name,
                price=to_decimal(named.group(1)) if named else price,
                currency=currency,
                is_free=not price and not named,
                pay_at_door=bool(named),
            )
        )
    return tiers


def sitemap_urls(xml: str, since: datetime) -> list[str]:
    """Event pages (``/e/``) of the sitemap last modified at or after ``since``, once each."""
    return list(
        dict.fromkeys(
            url
            for url, lastmod in _SITEMAP_URL.findall(xml)
            if "/e/" in url and (when := parse_iso(lastmod)) is not None and when >= since
        )
    )


def page_listing(text: str) -> dict[str, Any] | None:
    """The listing fields of an event page's own ``eventData`` (as the explore endpoint names them)."""
    at = text.find(_EVENT_DATA)
    try:
        data = json.JSONDecoder().raw_decode(text, at + len(_EVENT_DATA))[0] if at >= 0 else None
    except ValueError:
        data = None
    if not isinstance(data, dict) or not data.get("eventId"):
        return None
    return {
        "id": data["eventId"],
        "url": data.get("url"),
        "title": data.get("name"),
        "startDate": data.get("startUtc"),  # "start" is the wall time marked Z
        "endDate": data.get("endUtc"),
        "timezone": data.get("timezone"),
        "venueName": data.get("venueName"),
        "imageUrl": data.get("flyer"),
        "lineup": data.get("lineup"),
        "minTicketPrice": data.get("minPriceWithFees"),
        "groupName": _field(text, "groupName"),
    }


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
    m = re.search(rf'"{re.escape(key)}":("(?:[^"\\]|\\.)*")', text)
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
    if point and not (to_float(point.group(1)) or to_float(point.group(2))):
        point = None  # 0, 0: the place is revealed later, not the Gulf of Guinea
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
