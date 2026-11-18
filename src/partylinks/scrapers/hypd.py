"""hypd (hypd.co.il, "ההייפ מוצדק") - a curated party board for Israel: club nights, raves, festivals.

robots.txt allows everything but /admin, /api/, /go/ (its own ticket redirect), /sign/, /pickup/,
/preview and the query-string listings. So the events come from ``/sitemaps/events.xml`` (one
request, ~110 upcoming event pages; past ones sit in a separate ``past-events.xml`` that is never
read). Each URL ends in ``-YYYY-MM-DD-<id>``: pages dated before today are skipped unread.

Each event page (one request each) carries a schema.org ``MusicEvent`` JSON-LD: name, start/end with
offset, the place with street, city and coordinates, the artwork, performers (with the site's artist
page and picture) and one ``Offer`` whose url is the producer's own seller page (Zygo, Go-Out,
Eventer...), kept as ``extra["purchase_url"]`` so dedup joins that seller's listing. The genres are
the page's "עוד ב־" links to ``/genre/<slug>`` (טראנס, טכנו, האוס, מיינסטרים, פסטיבלים) plus its tag
chips ("אוויר פתוח"). The JSON-LD has no genre field.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import date, datetime
from functools import cache
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field
from selectolax.lexbor import LexborHTMLParser

from partylinks.artists import ArtistProfile
from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier, TierStatus
from partylinks.parsing import (
    ParseError,
    clean_str,
    has_time,
    html_to_text,
    jsonld_nodes,
    parse_iso,
    schema_types,
    to_decimal,
    to_float,
    zone,
)
from partylinks.places import PlaceIndex, israeli_places
from partylinks.retention import is_current
from partylinks.scrapers.abroad import country_code, local_zone
from partylinks.scrapers.base import BaseScraper, SourceSettings

_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_DATED = re.compile(r"-(\d{4})-(\d{2})-(\d{2})-(\d+)/?$")
_EVENT_TYPES = {"event", "musicevent", "festival", "danceevent"}
TYPE_HUBS = {"/nature": "nature party"}


class HypdSettings(SourceSettings):
    site_url: str = "https://hypd.co.il"
    sitemap_path: str = "/sitemaps/events.xml"
    # Unlinked tag chips are a genre ("אוויר פתוח") or the place ("רמת ישי", "אזור השרון"): places are
    # left out by the locality index, these regions by name. The age chip ("18+") by its digits.
    skip_tags: list[str] = Field(
        default_factory=lambda: [
            "כל הערים",
            "אזור השרון",
            "צפון הארץ",
            "דרום הארץ",
            "מרכז והשרון",
            "עמק חפר",
            "השפלה",
            "הגליל",
        ]
    )
    # "עוד ב־" hubs that are a kind of party but no /genre/ page, with the genre name they give
    # ("מסיבות טבע" as is would also read as the environment genre).
    type_hubs: dict[str, str] = Field(default_factory=lambda: dict(TYPE_HUBS))


class HypdScraper(BaseScraper[HypdSettings]):
    name = "hypd"
    settings_model = HypdSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        today = datetime.now(zone(self.timezone)).date()
        urls = event_urls(await self.get_text(s.site_url + s.sitemap_path), s.site_url, today)
        if not urls:
            raise ParseError("events sitemap lists no upcoming event")
        retention = RetentionSettings()

        async def fetch(url: str) -> ScrapedEvent | None:
            return parse_event(await self.get_text(url), url, self.timezone, s.skip_tags, s.type_hubs)

        async for event in self.stream_isolated(urls, fetch, label=lambda u: u):
            if event and is_current(
                event.start_at, event.end_at, retention, self.timezone, time_known=event.time_known
            ):
                yield event


def event_urls(xml: str, site_url: str, today: date) -> list[str]:
    """The sitemap's event pages not dated before today, once each, soonest first (undated last).

    The sitemap is not in date order (Nov 2026): sorted, a run a deploy cuts already holds the nearest nights.
    """
    out: dict[str, date] = {}
    prefix = site_url.rstrip("/") + "/event/"
    for url in _LOC.findall(xml):
        if not url.startswith(prefix):
            continue
        m = _DATED.search(urlsplit(url).path)
        on = date(int(m[1]), int(m[2]), int(m[3])) if m else date.max
        if on < today:
            continue
        out.setdefault(url, on)
    return sorted(out, key=out.__getitem__)


def parse_event(
    page_html: str, url: str, timezone: str, skip_tags: list[str] | None = None, type_hubs: dict[str, str] | None = None
) -> ScrapedEvent | None:
    """The page's own ``MusicEvent``; None when cancelled."""
    event = next((n for n in jsonld_nodes(page_html) if schema_types(n) & _EVENT_TYPES), None)
    if event is None:
        raise ParseError(f"{url}: no Event JSON-LD")
    if str(event.get("eventStatus") or "").endswith(("EventCancelled", "EventPostponed")):
        return None
    title = clean_str(str(event.get("name") or "").replace("\u200f", "").replace("\u200e", ""))
    location = event.get("location") if isinstance(event.get("location"), dict) else {}
    address = location.get("address") if isinstance(location.get("address"), dict) else {}
    country = country_code(clean_str(address.get("addressCountry")) or "IL")
    abroad = country not in (None, "IL")
    tz = local_zone(country, timezone) if abroad else timezone
    start, end = _local(event.get("startDate"), tz, abroad), _local(event.get("endDate"), tz, abroad)
    if not title or not start:
        raise ParseError(f"{url}: Event without name or start")
    geo = location.get("geo") if isinstance(location.get("geo"), dict) else {}
    lat, lon = to_float(geo.get("latitude")), to_float(geo.get("longitude"))
    offer = next((o for o in _list(event.get("offers")) if isinstance(o, dict)), {})
    low = to_decimal(offer.get("lowPrice") if "lowPrice" in offer else offer.get("price"))
    high = to_decimal(offer.get("highPrice")) or low
    sold_out = str(offer.get("availability") or "").endswith("SoldOut")
    seller = clean_str(offer.get("url"))
    if seller and urlsplit(seller).hostname in (None, urlsplit(url).hostname):
        seller = None  # hypd's own /go/ redirect: no seller page
    performers = [p for p in _list(event.get("performer")) if isinstance(p, dict) and clean_str(p.get("name"))]
    m = _DATED.search(urlsplit(url).path)
    return ScrapedEvent(
        source=HypdScraper.name,
        source_event_id=m[4] if m else urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1],
        url=clean_str(event.get("url")) or url,
        title=title,
        description=html_to_text(str(event.get("description") or "")),
        start_at=start,
        time_known=has_time(event.get("startDate")),
        end_at=end,
        venue_name=clean_str(location.get("name")),
        address=clean_str(address.get("streetAddress")),
        city=clean_str(address.get("addressLocality")),
        country=country if abroad else None,
        time_zone=tz if abroad else None,
        latitude=lat if lat is not None and lon is not None else None,
        longitude=lon if lat is not None and lon is not None else None,
        source_genres=page_genres(page_html, skip_tags or [], TYPE_HUBS if type_hubs is None else type_hubs),
        artists=[p["name"].strip() for p in performers],
        artist_profiles=[
            ArtistProfile(
                name=p["name"].strip(),
                source=HypdScraper.name,
                image_url=clean_str(p.get("image")) if isinstance(p.get("image"), str) else None,
                profile_url=clean_str(p.get("url")),
            )
            for p in performers
        ],
        images=[i for i in _list(event.get("image")) if isinstance(i, str) and "opengraph-image" not in i],
        currency=clean_str(offer.get("priceCurrency")) or "ILS",
        min_price=low,
        max_price=high,
        is_sold_out=sold_out,
        tickets=[TicketTier(name="כרטיס", price=low, status=TierStatus.SOLD_OUT if sold_out else TierStatus.ON_SALE)]
        if low is not None
        else [],
        extra={"purchase_url": urlunsplit(urlsplit(seller)._replace(query="", fragment=""))} if seller else {},
    )


def page_genres(page_html: str, skip_tags: list[str], type_hubs: dict[str, str]) -> list[str]:
    """The event's own /genre/ (and party-type) links in its tag row and "עוד ב־", and the tag chips that
    are no place, lower case, once each."""
    tree = LexborHTMLParser(page_html)
    found: list[str] = []
    for a in tree.css('nav.ev-portals a, [class*="event_tags"] a'):
        href = a.attributes.get("href") or ""
        text = a.text(strip=True) or ""
        if href.startswith("/genre/"):
            found.append(re.sub(r"^מסיבות\s+", "", text))  # "מסיבות טראנס" -> "טראנס"
        elif href in type_hubs:
            found.append(type_hubs[href])
    for chip in tree.css('[class*="event_tags"] > span'):
        text = chip.text(strip=True)
        if text and not any(ch.isdigit() for ch in text) and not _places().is_place(text):
            found.append(text)
    return list(dict.fromkeys(g.lower() for g in found if g and g not in skip_tags))


@cache
def _places() -> PlaceIndex:
    return PlaceIndex(list(israeli_places(0)))


def _local(value: Any, tz: str, abroad: bool) -> datetime | None:
    """A start or end. Abroad the site may stamp Israel's offset on the venue's wall time: the
    offset is dropped and the time read in the venue's zone (as airdrop)."""
    when = parse_iso(value, tz)
    return when.replace(tzinfo=None).replace(tzinfo=zone(tz)) if when is not None and abroad else when


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value] if value else []
