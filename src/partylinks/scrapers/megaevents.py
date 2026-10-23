"""Mega Events (mega-events.co.il) - packages (flight + hotel + ticket) to concerts and matches abroad.

robots.txt allows everything (asks for a 1 s crawl delay); one page is read per run.
Listing and details: the home page (Next.js App Router) embeds every active event in its React
Server Components payload: id, Hebrew and English name, date (no time), "city, country" in
Hebrew, ticket categories with supplier prices (TixStock) and the markups the site adds. Event
pages (``/order/<id>``) carry the same object, so no per-event request is needed.

The site's own "from" price (USD) is rebuilt from those fields: ticket-only events add
``ticket_only_markup`` to the cheapest available ticket; packages add the event markup, base
flight and hotel prices and a site-wide fee that is not in the payload (``package_fee``). The home
page's JSON-LD prices ~10 featured events; a mismatch with them is logged so a changed fee shows.
Football matches (most of the catalogue) are skipped. Every event gets ``country`` (ISO; "ZZ"
when the name is unknown), which keeps it out of the Israeli feed and in the abroad section.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from decimal import Decimal
from functools import cache
from typing import Any

from partylinks.models import ScrapedEvent
from partylinks.parsing import ParseError, clean_str, jsonld_nodes, parse_iso, to_decimal, to_float
from partylinks.scrapers.abroad import airports, country_code, local_zone
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.popit import flight_payload

_EVENT_START = re.compile(r'\{"id":\d+,"created_at"')


class MegaeventsSettings(SourceSettings):
    site_url: str
    event_page_path: str  # must contain "{event_id}"
    package_fee: Decimal = Decimal(175)  # USD the site adds to every package (not in the payload)


class MegaeventsScraper(BaseScraper[MegaeventsSettings]):
    name = "megaevents"
    settings_model = MegaeventsSettings
    abroad_only = True

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        page = await self.get_text(s.site_url + "/")
        raw_events = event_objects(flight_payload(page))
        if not raw_events:
            raise ParseError("no event objects in the home page payload")
        self.log.info("listed %d events", len(raw_events))
        featured = featured_prices(page)
        for raw in raw_events:
            try:
                event = parse_event(raw, s.site_url + s.event_page_path, self.timezone, s.package_fee)
            except Exception as exc:  # noqa: BLE001 - one bad item never drops the rest
                self.record_failure(f"event {raw.get('id')}", exc)
                continue
            if event is None:
                continue
            shown = featured.get((raw.get("name"), raw.get("date")))
            if shown is not None and event.min_price is not None and shown != event.min_price:
                self.log.warning(
                    "event %s: computed price %s, site shows %s (package_fee changed?)",
                    raw["id"],
                    event.min_price,
                    shown,
                )
            yield event


def event_objects(rsc: str) -> list[dict[str, Any]]:
    """Every distinct event object (an ``{"id":<int>,"created_at":...`` row) in the payload."""
    decoder = json.JSONDecoder()
    found: dict[int, dict[str, Any]] = {}
    for match in _EVENT_START.finditer(rsc):
        try:
            obj, _ = decoder.raw_decode(rsc, match.start())
        except ValueError:
            continue
        if isinstance(obj, dict) and "date" in obj and "tickets_and_rates" in obj:
            found.setdefault(obj["id"], obj)
    return list(found.values())


def featured_prices(page_html: str) -> dict[tuple[str, str], Decimal]:
    """(name, date) -> the "from" price the home page's JSON-LD offer catalog shows."""
    prices: dict[tuple[str, str], Decimal] = {}
    for node in jsonld_nodes(page_html):
        for offer in (node.get("hasOfferCatalog") or {}).get("itemListElement") or []:
            item = offer.get("itemOffered") or {}
            price = to_decimal(offer.get("price"))
            if price is not None and item.get("name") and item.get("startDate"):
                prices[(item["name"], item["startDate"])] = price
    return prices


@cache
def _airport_cities() -> frozenset[str]:
    return frozenset(a["city"].casefold() for a in airports().values() if a.get("city"))


def _artist(name_english: str | None) -> str | None:
    """'Harry Styles London' -> 'Harry Styles': the English name carries a city on some dates."""
    # ponytail: a trailing word that is an airport city is taken as the city; an act whose name
    # ends in a city name loses that word. Match against the Hebrew city if that ever bites.
    name = clean_str(name_english)
    if name and " " in name and name.rsplit(" ", 1)[1].casefold() in _airport_cities():
        return name.rsplit(" ", 1)[0]
    return name


def _is_sport(raw: dict[str, Any]) -> bool:
    return bool(raw.get("match_home_logo_url")) or " vs " in str(raw.get("name_english") or "").casefold()


def parse_event(
    raw: dict[str, Any], page_url_template: str, timezone: str, package_fee: Decimal
) -> ScrapedEvent | None:
    """The event; None for a football match or a hidden / deleted / test event."""
    if (
        _is_sport(raw)
        or raw.get("is_hidden")
        or raw.get("is_deleted")
        or raw.get("is_test")
        or raw.get("deactivated_at")
    ):
        return None
    title = clean_str(raw.get("name"))
    if not raw.get("id") or not title:
        raise ParseError("event id/name missing")
    location = raw.get("location") if isinstance(raw.get("location"), dict) else {}
    city, _, country_name = (clean_str(location.get("name")) or "").rpartition(",")
    country = country_code(clean_str(location.get("country_code")) or country_name)
    start_at = parse_iso(raw.get("date"), local_zone(country, timezone))  # a date alone: the time is unknown
    if start_at is None:
        raise ParseError(f"event {raw['id']}: no date")

    tickets = [t for t in raw.get("tickets_and_rates") or [] if isinstance(t, dict) and t.get("available")]
    cheapest = min((p for t in tickets if (p := to_decimal(t.get("price")))), default=None)
    min_price = None
    if cheapest is not None:
        if raw.get("package_mode") == "ticket_only":
            min_price = cheapest + (to_decimal(raw.get("ticket_only_markup")) or 0)
        else:
            extras = ("event_additional_markup", "base_flight_price", "base_hotel_price")
            min_price = cheapest + sum((to_decimal(raw.get(k)) or Decimal(0) for k in extras), package_fee)
    artist = _artist(raw.get("name_english"))
    images = [u for k in ("card_image_url", "campaign_banner_url") if (u := clean_str(raw.get(k)))]
    return ScrapedEvent(
        source=MegaeventsScraper.name,
        source_event_id=str(raw["id"]),
        url=page_url_template.format(event_id=raw["id"]),
        title=title,
        source_genres=["concert"],  # football is skipped: every kept event is a concert
        description=clean_str(raw.get("description")),
        start_at=start_at,
        city=clean_str(city) or None,
        latitude=to_float(location.get("latitude")),  # the city's centre, not the venue
        longitude=to_float(location.get("longitude")),
        country=None if country == "IL" else country,
        artists=[artist] if artist else [],
        images=images,
        currency="USD",
        min_price=min_price,
        is_sold_out=not tickets,
        extra={
            k: v
            for k, v in {
                "package": raw.get("package_mode") != "ticket_only",
                "name_english": clean_str(raw.get("name_english")),
                "tags": clean_str(raw.get("tags")),
                "depart": raw.get("def_date_depart"),
                "return": raw.get("def_date_return"),
            }.items()
            if v
        },
    )
