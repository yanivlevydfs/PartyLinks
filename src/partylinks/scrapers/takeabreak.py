"""Take A Break (takebreaktravel.com) - an Israeli agency's packages (flight + hotel + ticket) to club
nights, concerts, festivals and matches abroad (Dubai, Mykonos, Athens, Limassol, London...).

robots.txt allows everything but /admin/, /api/, /quote/, /review/ and /seats. The events page
(``/events``, one request) carries an ``ItemList`` JSON-LD of the upcoming events only (the sitemap
also keeps past ones), and each card a kind badge (הופעה, פסטיבל, ספורט), kept as the genre.

Each event page (one request each, ~15) carries a schema.org ``Event`` JSON-LD: name, start with
the venue's offset, the venue with its city (Hebrew) and country code, the artwork and the
performers (with the site's artist page and picture). The page's React payload lists the hotels of
the package, each with a per-person price in ILS ("priceLabel") and a sold-out flag: the cheapest
and dearest open hotel give the price range. There are no coordinates: the venue and city geocode.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import datetime
from decimal import Decimal
from typing import Any

from selectolax.lexbor import LexborHTMLParser

from partylinks.artists import ArtistProfile
from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import (
    ParseError,
    clean_str,
    has_time,
    html_to_text,
    jsonld_nodes,
    og_image,
    parse_iso,
    schema_types,
    to_decimal,
    zone,
)
from partylinks.retention import is_current
from partylinks.scrapers.abroad import country_code, local_zone
from partylinks.scrapers.base import BaseScraper, SourceSettings

_EVENT_TYPES = {"event", "musicevent", "festival", "danceevent", "sportsevent"}
# One hotel of the package in the page's React payload (escaped once inside a JS string).
_HOTEL = re.compile(r'priceLabel\\?"\s*:\s*\\?"([^"\\]*)\\?"\s*,[^{}]{0,400}?soldOut\\?"\s*:\s*(true|false)')
_SPAN = re.compile(r"(\d{1,2})(?:\.(\d{1,2}))?\s*[-–]\s*(\d{1,2})\.(\d{1,2})\.(20\d\d)")
_CANCELLED = "בוטל"
_DAY = re.compile(r"(\d{1,2})\.(\d{1,2})\.(20\d\d)")
_MARKS = re.compile("[\u200e\u200f\u202a-\u202e]")


class TakeABreakSettings(SourceSettings):
    site_url: str = "https://takebreaktravel.com"
    listing_path: str = "/events"


class TakeABreakScraper(BaseScraper[TakeABreakSettings]):
    name = "takeabreak"
    settings_model = TakeABreakSettings
    abroad_only = True

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        listing = parse_listing(await self.get_text(s.site_url + s.listing_path), s.site_url)
        if not listing:
            raise ParseError("events page lists no event")
        self.log.debug("listed %d events", len(listing))
        retention = RetentionSettings()

        async def fetch(item: tuple[str, list[str]]) -> ScrapedEvent | None:
            url, kinds = item
            return parse_event(await self.get_text(url), url, kinds, self.timezone)

        async for event in self.stream_isolated(list(listing.items()), fetch, label=lambda i: i[0]):
            if event and is_current(
                event.start_at, event.end_at, retention, self.timezone, time_known=event.time_known
            ):
                yield event


def parse_listing(page_html: str, site_url: str) -> dict[str, list[str]]:
    """The upcoming events (the ``ItemList``) in order, each with its card's kind badge."""
    base = site_url.rstrip("/")
    urls: list[str] = []
    for node in jsonld_nodes(page_html):
        if "itemlist" in schema_types(node):
            for item in node.get("itemListElement") or []:
                url = clean_str(item.get("url")) if isinstance(item, dict) else None
                if url and url.startswith(base + "/events/"):
                    urls.append(url)
    kinds: dict[str, list[str]] = {}
    for link in LexborHTMLParser(page_html).css('a[href^="/events/"]'):
        card = link.parent
        badge = card.css_first("div.backdrop-blur") if card is not None else None
        text = clean_str(badge.text(strip=True)) if badge is not None else None
        if text:
            kinds.setdefault(base + (link.attributes.get("href") or ""), []).append(text)
    return {u: list(dict.fromkeys(kinds.get(u, []))) for u in dict.fromkeys(urls)}


def parse_event(page_html: str, url: str, kinds: list[str], timezone: str) -> ScrapedEvent | None:
    """The page's own ``Event`` (or, for a festival trip whose ticket is sold apart, its ``TouristTrip``
    over the trip's dates) and the package price; None when cancelled."""
    nodes = jsonld_nodes(page_html)
    event = next((n for n in nodes if schema_types(n) & _EVENT_TYPES), None)
    trip = None if event else next((n for n in nodes if "touristtrip" in schema_types(n)), None)
    if event is None and trip is None:
        if _CANCELLED in (_title(page_html) or ""):
            return None  # a cancelled event's page keeps only its title ("... — בוטל · ...")
        raise ParseError(f"{url}: no Event or TouristTrip JSON-LD")
    node: dict[str, Any] = event or trip or {}
    if str(node.get("eventStatus") or "").endswith(("EventCancelled", "EventPostponed")):
        return None
    if event:
        title = clean_str(_MARKS.sub("", str(event.get("name") or "")))
        location = event.get("location") if isinstance(event.get("location"), dict) else {}
    else:
        title = _crumb(nodes, url) or clean_str(_MARKS.sub("", str(node.get("name") or "")))
        location = node.get("itinerary") if isinstance(node.get("itinerary"), dict) else {}
    address = location.get("address") if isinstance(location.get("address"), dict) else {}
    street = clean_str(address.get("streetAddress"))
    country = country_code(clean_str(address.get("addressCountry")) or (street or "").rsplit(",", 1)[-1].strip())
    city = clean_str(address.get("addressLocality")) or (None if event else clean_str(location.get("name")))
    tz = local_zone(country, timezone, city) if country != "IL" else timezone
    if event:
        start, end = _local(event.get("startDate"), tz), _local(event.get("endDate"), tz)
        time_known = has_time(event.get("startDate"))
    else:
        start, end = trip_dates(str(node.get("name") or ""), tz)
        time_known = False
    if not title or not start:
        raise ParseError(f"{url}: no name or date")
    performers = [p for p in _list(node.get("performer")) if isinstance(p, dict) and clean_str(p.get("name"))]
    low, high, currency = offer_prices(node.get("offers"))
    if low is None:
        (low, high), currency = package_prices(page_html), "ILS"
    images = [i for i in _list(node.get("image")) if isinstance(i, str)]
    if not images and (og := og_image(page_html)) and "og-default" not in og:
        images = [og]  # a trip page: the destination's picture
    abroad = country != "IL"
    return ScrapedEvent(
        source=TakeABreakScraper.name,
        source_event_id=url.rstrip("/").rsplit("/", 1)[-1],
        url=clean_str(node.get("url")) or url,
        title=title,
        description=html_to_text(str(node.get("description") or "")),
        start_at=start,
        time_known=time_known,
        end_at=end if end and end > start else None,
        venue_name=clean_str(location.get("name")) if event else None,
        address=street,
        city=city,
        country=country if abroad else None,
        time_zone=tz if abroad else None,
        source_genres=[k.lower() for k in kinds],
        artists=[p["name"].strip() for p in performers],
        artist_profiles=[
            ArtistProfile(
                name=p["name"].strip(),
                source=TakeABreakScraper.name,
                image_url=clean_str(p.get("image")) if isinstance(p.get("image"), str) else None,
                profile_url=clean_str(p.get("url")),
            )
            for p in performers
        ],
        images=images,
        currency=currency,
        min_price=low,
        max_price=high,
        extra={"package": True},
    )


def offer_prices(offers: Any) -> tuple[Decimal | None, Decimal | None, str]:
    """The JSON-LD offer's "from" price (or low / high) and its currency."""
    offer = next((o for o in _list(offers) if isinstance(o, dict)), {})
    low = to_decimal(offer.get("lowPrice") if "lowPrice" in offer else offer.get("price"))
    high = to_decimal(offer.get("highPrice")) or low
    return low or None, high or None, clean_str(offer.get("priceCurrency")) or "ILS"


def trip_dates(name: str, tz: str) -> tuple[datetime | None, datetime | None]:
    """The trip's days from its name: "(27-31.5.2027)", "(30.5-2.6.2027)" or "28.5.2027"."""
    when = None
    if m := _SPAN.search(name):
        d1, m1, d2, m2, year = m.groups()
        when = (int(year), int(m1 or m2), int(d1)), (int(year), int(m2), int(d2))
    elif m := _DAY.search(name):
        day, month, year = map(int, m.groups())
        when = (year, month, day), None
    if when is None:
        return None, None
    try:
        start = datetime(*when[0], tzinfo=zone(tz))
        end = datetime(*when[1], tzinfo=zone(tz)) if when[1] else None
    except ValueError:
        return None, None
    return start, end if end and end > start else None


def _title(page_html: str) -> str | None:
    node = LexborHTMLParser(page_html).css_first("title")
    return node.text(strip=True) if node is not None else None


def _crumb(nodes: list[dict[str, Any]], url: str) -> str | None:
    """The breadcrumb's name of this page ("זמנה לימסול 2027")."""
    for node in nodes:
        if "breadcrumblist" in schema_types(node):
            for item in node.get("itemListElement") or []:
                if isinstance(item, dict) and str(item.get("item") or "").rstrip("/") == url.rstrip("/"):
                    return clean_str(_MARKS.sub("", str(item.get("name") or "")))
    return None


def package_prices(page_html: str) -> tuple[Decimal | None, Decimal | None]:
    """Cheapest and dearest per-person price of the package's open hotels (ILS)."""
    prices = []
    for label, sold_out in _HOTEL.findall(page_html):
        digits = re.sub(r"\D", "", label)
        if digits and sold_out == "false":
            prices.append(Decimal(digits))
    return (min(prices), max(prices)) if prices else (None, None)


def _local(value: Any, tz: str) -> datetime | None:
    """A start or end in the venue's zone."""
    when = parse_iso(value, tz)
    return when.astimezone(zone(tz)) if when else None


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value] if value else []
