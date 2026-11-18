"""Xceed (xceed.me) - a Barcelona-born ticketing app for clubs and club nights in Spain, Italy,
Portugal and Germany.

Only the cities Israelis fly to most are read (config ``cities``). robots.txt disallows only
/reset and /authenticate. The site's API needs a key, so the public pages are read:

- A city's events page (``/en/<city>/events``, Next.js, one request) embeds its ~24 top upcoming
  events in the React Query state (``"all-events"``): id, name, slug, start / end (unix), artwork,
  venue with the city's time zone, lineup and music genres. The genres are kept from here.
- Each event's page (``/en/<city>/event/<slug>/<id>``, one request each) carries a schema.org
  ``Event`` JSON-LD: the venue with street address and coordinates, the ticket offers (name,
  price, currency, availability), performers and the description.

A run is cities x (1 + ~24) requests (~270 for eleven cities). Events whose start has passed are
dropped from the listing before their page is read.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Iterable
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import Field

from partylinks.artists import ArtistProfile
from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier, TierStatus
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
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_PUSH = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', re.DOTALL)
_EVENT_TYPES = {"event", "musicevent", "danceevent", "festival"}


class XceedSettings(SourceSettings):
    site_url: str = "https://xceed.me"
    lang: str = "en"
    # Xceed's city slugs, only the destinations Israelis fly to most.
    cities: list[str] = Field(
        default_factory=lambda: [
            "barcelona",
            "madrid",
            "milano",
            "roma",
            "lisboa",
            "porto",
            "berlin",
            "palma-de-mallorca",
            "valencia",
            "tenerife",
            "napoli",
        ]
    )
    # A night with no music genre on the listing is still a club night.
    default_genre: str = "nightlife"


class XceedScraper(BaseScraper[XceedSettings]):
    name = "xceed"
    settings_model = XceedSettings
    abroad_only = True

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        now = datetime.now(zone(self.timezone)).timestamp()
        items: dict[int, dict[str, Any]] = {}
        for city in s.cities:
            url = f"{s.site_url}/{s.lang}/{city}/events"
            page = await self.optional(url, self.get_text(url))
            try:
                listed = city_events(page) if page else []
            except ParseError as exc:  # a changed page: the other cities still count
                self.record_failure(url, exc)
                continue
            for item in listed:
                ends = item.get("endingTime") or item.get("startingTime") or 0
                if isinstance(ends, int | float) and ends > now:
                    items.setdefault(item["legacyId"], item)
        if not items:
            raise ParseError("no city page lists an event")
        self.log.debug("listed %d events", len(items))
        retention = RetentionSettings()

        async def fetch(item: dict[str, Any]) -> ScrapedEvent | None:
            url = event_url(item, s.site_url, s.lang)
            return parse_event(await self.get_text(url), item, url, s.default_genre, self.timezone)

        # Soonest first across cities (Nov 2026): a run cut half-way already holds the nearest nights.
        async for event in self.stream_isolated(soonest_first(items.values()), fetch, label=lambda i: str(i["legacyId"])):
            if event and is_current(event.start_at, event.end_at, retention, self.timezone):
                yield event


def city_events(page_html: str) -> list[dict[str, Any]]:
    """The ``all-events`` query's items in a city page's React state."""
    flight = "".join(json.loads(f'"{chunk}"') for chunk in _PUSH.findall(page_html))
    at = flight.find('"all-events"')
    if at < 0:
        raise ParseError("city page has no all-events query")
    start = flight.rfind('{"dehydratedAt"', 0, at)
    if start < 0:
        raise ParseError("all-events query without its state")
    query, _ = json.JSONDecoder().raw_decode(flight, start)
    pages = (((query.get("state") or {}).get("data") or {}).get("pages")) or []
    return [
        item
        for page in pages
        if isinstance(page, dict)
        for item in page.get("items") or []
        if isinstance(item, dict) and isinstance(item.get("legacyId"), int) and clean_str(item.get("slug"))
    ]


def soonest_first(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Listing items by start time, ascending; one without a start last."""
    return sorted(items, key=lambda i: s if isinstance(s := i.get("startingTime"), int | float) else float("inf"))


def event_url(item: dict[str, Any], site_url: str, lang: str) -> str:
    city = ((item.get("venue") or {}).get("city") or {}).get("slug") or "events"
    return f"{site_url}/{lang}/{city}/event/{item['slug']}/{item['legacyId']}"


def parse_event(
    page_html: str, item: dict[str, Any], url: str, default_genre: str, timezone: str
) -> ScrapedEvent | None:
    """The event page's JSON-LD with the listing's genres; None when cancelled."""
    node = next((n for n in jsonld_nodes(page_html) if schema_types(n) & _EVENT_TYPES), None)
    if node is None:
        raise ParseError(f"{url}: no Event JSON-LD")
    if str(node.get("eventStatus") or "").endswith(("EventCancelled", "EventPostponed")):
        return None
    venue = item.get("venue") if isinstance(item.get("venue"), dict) else {}
    tz = clean_str((venue.get("city") or {}).get("timezone")) or timezone
    start, end = parse_iso(node.get("startDate"), tz), parse_iso(node.get("endDate"), tz)
    title = clean_str(node.get("name")) or clean_str(item.get("name"))
    if not title or not start:
        raise ParseError(f"{url}: Event without name or start")
    start, end = start.astimezone(zone(tz)), end.astimezone(zone(tz)) if end else None
    place = node.get("location") if isinstance(node.get("location"), dict) else {}
    address = place.get("address") if isinstance(place.get("address"), dict) else {}
    geo = place.get("geo") if isinstance(place.get("geo"), dict) else {}
    lat, lon = to_float(geo.get("latitude")), to_float(geo.get("longitude"))
    country = (clean_str(address.get("addressCountry")) or "ZZ").upper()
    tiers = offer_tiers(node.get("offers"))
    open_prices = [t.price for t in tiers if t.price is not None and t.status == TierStatus.ON_SALE]
    prices = open_prices or [t.price for t in tiers if t.price is not None]
    sold_out = bool(tiers) and all(t.status == TierStatus.SOLD_OUT for t in tiers)
    performers = [p for p in _list(node.get("performer")) if isinstance(p, dict) and clean_str(p.get("name"))]
    genres = [g["name"] for g in item.get("musicGenres") or [] if isinstance(g, dict) and clean_str(g.get("name"))]
    images = [i for i in _list(node.get("image")) if isinstance(i, str)][:1] or (
        [item["coverUrl"]] if clean_str(item.get("coverUrl")) else []
    )
    currency = next(
        (c for o in _list(node.get("offers")) if isinstance(o, dict) for c in [clean_str(o.get("priceCurrency"))] if c),
        None,
    )
    return ScrapedEvent(
        source=XceedScraper.name,
        source_event_id=str(item["legacyId"]),
        url=clean_str(node.get("url")) or url,
        title=title,
        description=html_to_text(str(node.get("description") or "")),
        start_at=start,
        end_at=end if end and end > start else None,
        time_known=True,
        venue_name=clean_str(place.get("name")) or clean_str(venue.get("name")),
        address=clean_str(address.get("streetAddress")),
        city=clean_str(address.get("addressLocality")) or clean_str((venue.get("city") or {}).get("name")),
        country=None if country == "IL" else country,
        time_zone=None if country == "IL" else tz,
        latitude=lat if lat is not None and lon is not None else None,
        longitude=lon if lat is not None and lon is not None else None,
        source_genres=list(dict.fromkeys(g.strip().lower() for g in genres)) or [default_genre],
        artists=[p["name"].strip() for p in performers],
        artist_profiles=[
            ArtistProfile(
                name=p["name"].strip(),
                source=XceedScraper.name,
                image_url=next((i for i in _list(p.get("image")) if isinstance(i, str)), None),
                profile_url=clean_str(p.get("url")),
            )
            for p in performers
        ],
        images=images,
        currency=currency or "EUR",  # the cities read are in the eurozone
        min_price=min(prices) if prices else None,
        max_price=max(prices) if prices else None,
        is_sold_out=sold_out,
        tickets=tiers,
    )


def offer_tiers(offers: Any) -> list[TicketTier]:
    """One tier per named offer, in the page's order."""
    tiers: list[TicketTier] = []
    for offer in _list(offers):
        if not isinstance(offer, dict):
            continue
        price: Decimal | None = to_decimal(offer.get("price"))
        sold = str(offer.get("availability") or "").endswith("SoldOut")
        tiers.append(
            TicketTier(
                name=clean_str(offer.get("name")) or "Ticket",
                price=price,
                status=TierStatus.SOLD_OUT if sold else TierStatus.ON_SALE,
            )
        )
    return tiers


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value] if value else []
