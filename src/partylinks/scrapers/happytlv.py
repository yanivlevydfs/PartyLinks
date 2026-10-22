"""HappyTLV (happytlv.com) - Tel Aviv night-out guide: parties and bars' happy hours.

robots.txt allows everything but ``/_api/``. One sitemap lists every page; two kinds are read:

* Parties: ``/tel-aviv-parties/<venue>-<yyyy-mm-dd>[-<id>]``. Only upcoming dates are fetched
  (the date is in the path); each page's schema.org ``Event`` gives name, start/end, place,
  organizer, age, image and one ``Offer`` with the price and the seller's link (Go-Out, Eventer...),
  kept as ``extra["purchase_url"]`` (without HappyTLV's referral) so dedup ties it to that seller's
  own listing. Hub pages (lines, venues, music, holidays) have no date in the path and are skipped.
* Happy hours: ``/tel-aviv-happy-hours/<bar>``. A bar's page is a schema.org ``BarOrPub`` /
  ``Restaurant`` with address, coordinates, image and ``makesOffer``: the deal ("30% על שתייה
  ואוכל") and its weekly hours. A happy hour is a weekly deal, not an event, so it becomes one
  event per day it runs over the next ``happy_hour_days`` days, under the "happy_hour" category.
  Hub pages (areas, themes: ``/dizengoff``, ``/date``...) are ``ItemList`` pages and are skipped.
"""

from __future__ import annotations

import hashlib
import html
import re
from collections.abc import AsyncIterator
from datetime import date, datetime, time, timedelta
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

from pydantic import Field

from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import (
    ParseError,
    clean_str,
    html_to_text,
    jsonld_nodes,
    parse_iso,
    schema_types,
    to_decimal,
    to_float,
    zone,
)
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.venues import PLACE_LISTING

_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_PARTY_DATE = re.compile(r"-(\d{4}-\d{2}-\d{2})(?:-\d+)?/?$")
_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]  # date.weekday() order
_PLACES = {"barorpub", "restaurant", "foodestablishment", "cafeorcoffeeshop", "localbusiness", "nightclub"}
HAPPY_HOUR = "happy hour"


class HappyTlvSettings(SourceSettings):
    site_url: str
    sitemap_path: str
    parties_path: str  # /tel-aviv-parties/
    happy_hours_path: str  # /tel-aviv-happy-hours/
    # A happy hour becomes one event per day it runs, this many days ahead (today included).
    happy_hour_days: int = Field(7, ge=1, le=31)
    max_pages: int = Field(600, ge=1)


class HappyTlvScraper(BaseScraper[HappyTlvSettings]):
    name = "happytlv"
    settings_model = HappyTlvSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        today = datetime.now(zone(self.timezone)).date()
        parties, bars = split_sitemap(await self.get_text(s.site_url + s.sitemap_path), s, today)
        pages = [*parties, *bars]
        party_pages = set(parties)
        if len(pages) > s.max_pages:
            self.log.warning("listing truncated at max_pages=%d (%d found)", s.max_pages, len(pages))
        self.log.info("listed %d upcoming parties and %d happy-hour pages", len(parties), len(bars))

        async def fetch(url: str) -> list[ScrapedEvent]:
            page = await self.get_text(url)
            if url in party_pages:
                party = parse_party(page, url, self.timezone)
                return [party] if party else []
            return parse_happy_hours(page, url, self.timezone, today, s.happy_hour_days)

        async for events in self.stream_isolated(pages[: s.max_pages], fetch, label=lambda u: unquote(u)):
            for event in events or []:
                yield event


def split_sitemap(sitemap: str, s: HappyTlvSettings, today: date) -> tuple[list[str], list[str]]:
    """(upcoming party pages, happy-hour pages) of the sitemap, each once."""
    parties: dict[str, None] = {}
    bars: dict[str, None] = {}
    for url in map(html.unescape, _LOC.findall(sitemap)):  # XML entities: &apos; &amp;
        path = urlsplit(url).path
        if path.startswith(s.parties_path):
            day = _PARTY_DATE.search(path)
            if day and date.fromisoformat(day.group(1)) >= today - timedelta(days=1):
                parties[url] = None
        elif path.startswith(s.happy_hours_path) and path.rstrip("/") != s.happy_hours_path.rstrip("/"):
            bars[url] = None
    return list(parties), list(bars)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value] if value else []


def _without_query(url: str | None) -> str | None:
    """A seller's link without HappyTLV's referral parameters."""
    return urlunsplit(urlsplit(url)._replace(query="", fragment="")) if url else None


def parse_party(page_html: str, url: str, timezone: str) -> ScrapedEvent | None:
    """The page's ``Event``; None when cancelled."""
    event = next((n for n in jsonld_nodes(page_html) if "event" in schema_types(n)), None)
    if event is None:
        raise ParseError(f"{url}: no Event JSON-LD")
    if str(event.get("eventStatus") or "").endswith("EventCancelled"):
        return None
    title, start = clean_str(event.get("name")), parse_iso(event.get("startDate"), timezone)
    if not title or not start:
        raise ParseError(f"{url}: Event without name or start")
    place = _dict(event.get("location"))
    address = _dict(place.get("address"))
    offer = next((o for o in _list(event.get("offers")) if isinstance(o, dict)), {})
    price = to_decimal(offer.get("price"))
    age = re.match(r"\s*(\d+)", str(event.get("typicalAgeRange") or ""))
    return ScrapedEvent(
        source=HappyTlvScraper.name,
        source_event_id=urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1],
        url=url,
        title=title,
        description=html_to_text(event.get("description")),
        start_at=start,
        end_at=parse_iso(event.get("endDate"), timezone),
        venue_name=clean_str(place.get("name")),
        address=clean_str(address.get("streetAddress")),
        city=clean_str(address.get("addressLocality")),
        organizer=clean_str(_dict(event.get("organizer")).get("name")),
        min_age=int(age.group(1)) if age else None,
        images=[i for i in _list(event.get("image")) if isinstance(i, str)],
        currency=clean_str(offer.get("priceCurrency")) or "ILS",
        tickets=[TicketTier(name="כרטיס", price=price, is_free=price == 0)] if price is not None else [],
        extra={"purchase_url": _without_query(clean_str(offer.get("url")))} if offer.get("url") else {},
    )


def _clock(value: Any) -> time | None:
    match = re.match(r"\s*(\d{1,2}):(\d{2})", str(value or ""))
    return time(int(match.group(1)) % 24, int(match.group(2))) if match else None


def parse_happy_hours(page_html: str, url: str, timezone: str, today: date, days: int) -> list[ScrapedEvent]:
    """One event per day the bar's happy hour runs, ``days`` days from ``today``; [] for hub pages."""
    place = next((n for n in jsonld_nodes(page_html) if schema_types(n) & _PLACES and n.get("makesOffer")), None)
    if place is None:
        return []
    name = clean_str(place.get("name"))
    offer = next((o for o in _list(place.get("makesOffer")) if isinstance(o, dict)), {})
    service = _dict(offer.get("itemOffered"))
    if not name or not service:
        return []
    address, geo = _dict(place.get("address")), _dict(place.get("geo"))
    deal = clean_str(offer.get("description"))
    tz = zone(timezone)
    key = hashlib.sha1(url.encode()).hexdigest()[:10]
    events = []
    for spec in (s for s in _list(service.get("hoursAvailable")) if isinstance(s, dict)):
        opens, closes = _clock(spec.get("opens")), _clock(spec.get("closes"))
        weekdays = {str(d).rsplit("/", 1)[-1] for d in _list(spec.get("dayOfWeek"))}
        if opens is None or closes is None:
            continue
        for day in (today + timedelta(days=n) for n in range(days)):
            if _DAYS[day.weekday()] not in weekdays:
                continue
            start = datetime.combine(day, opens, tz)
            end = datetime.combine(day + timedelta(days=1) if closes <= opens else day, closes, tz)
            events.append(
                ScrapedEvent(
                    source=HappyTlvScraper.name,
                    source_event_id=f"hh-{key}-{day.isoformat()}-{opens:%H%M}",
                    url=url,
                    title=clean_str(offer.get("name")) or f"האפי האוור ב{name}",
                    description=deal,
                    start_at=start,
                    end_at=end,
                    venue_name=name,
                    address=clean_str(address.get("streetAddress")),
                    city=clean_str(address.get("addressLocality")),
                    latitude=to_float(geo.get("latitude")),
                    longitude=to_float(geo.get("longitude")),
                    images=[i for i in _list(place.get("image")) if isinstance(i, str)],
                    source_genres=[HAPPY_HOUR],
                    event_types=[HAPPY_HOUR],
                    extra={
                        PLACE_LISTING: True,
                        **({"deal": deal} if deal else {}),
                        **({"phone": p} if (p := clean_str(place.get("telephone"))) else {}),
                    },
                )
            )
    return events
