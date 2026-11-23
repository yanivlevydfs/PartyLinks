"""Parties24/7 (parties247.co.il) - a national party guide (Next.js on Vercel), mostly Go-Out's parties.

robots.txt allows everything but ``/admin/``. The parties are its events sitemap (``/sitemaps/events.xml``,
every coming party: 96 on 24.11.2026, the holiday ones months ahead; the ``archive-*`` ones are past) and
the lists the home page and the static sitemap link (``listing_prefixes``: days, cities, clubs, carousels,
audiences, genres, DJs, the SEO ``/parties/`` lists, the holiday pages, ``/all-parties``), each of which
links its parties (``/event/<slug>``): ~100 events over ~55 pages, ~160 requests a run. The lists give the
categories and a city; the sitemap the parties no list shows yet (Purim 2027, New Year's Eve...).

Each event page carries a schema.org ``Event`` (JSON-LD): name, start (with its offset), image, a short
description, ``eventStatus`` (cancelled ones are dropped) and an ``Offer``: price and the seller's page,
almost always Go-Out's (``extra["purchase_url"]``, its ``ref`` affiliate code dropped: dedup merges it
with the ``goout`` listing). The location is one text, "<venue>, <street>, <city>, ישראל"; some give
only a street address. Categories: the genre lists an event appears on (``/genre/techno-music`` ->
"techno music"), and the audience lists ("24plus parties", "teenage parties").
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

from pydantic import Field

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, html_to_text, jsonld_nodes, parse_iso, to_decimal
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_EVENT_LINK = re.compile(r'href="(/event/[^"#?]+)"')
_COUNTRY = {"ישראל", "israel", "israël"}
_NOT_VENUES = {
    "מחוז הצפון",
    "מחוז הדרום",
    "מחוז המרכז",
    "מחוז חיפה",
    "מחוז ירושלים",
    "מחוז תל אביב",
    "מרכז",
    "צפון",
    "דרום",
}


class Parties247Settings(SourceSettings):
    site_url: str = "https://www.parties247.co.il"
    # The home page's links to lists of parties: every path starting with one of these is read.
    listing_prefixes: list[str] = Field(
        default_factory=lambda: [
            "/all-parties", "/day/", "/cities/", "/club/", "/carousels/", "/audience/", "/genre/", "/dj/", "/parties/",
            "/friday-parties", "/saturday-parties", "/thursday-parties", "/weekend-parties", "/boutique-parties",
            "/rosh-hashana", "/sukkot", "/halloween", "/hanukkah", "/sylvester", "/purim", "/yom-haatzmaut",
        ]
    )  # fmt: skip
    # Sitemaps: the events one lists every coming party; the static one the lists (read when a prefix matches).
    events_sitemap: str = "/sitemaps/events.xml"
    static_sitemap: str = "/sitemaps/static.xml"
    # Lists whose name is a category of the parties on them (their slug, "techno-music" -> "techno music").
    category_prefixes: list[str] = Field(default_factory=lambda: ["/genre/", "/audience/"])
    # City lists -> their city: a party whose location names a street only takes it from the list it is on.
    city_lists: dict[str, str] = Field(default_factory=dict)


class Parties247Scraper(BaseScraper[Parties247Settings]):
    name = "parties247"
    settings_model = Parties247Settings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        home = await self.get_text(s.site_url + "/")
        static = await self.optional("static sitemap", self.get_text(s.site_url + s.static_sitemap))
        lists = list(dict.fromkeys(listing_paths(home, s.listing_prefixes) + sitemap_paths(static, s.listing_prefixes)))
        towns = _town_names()
        found: dict[str, set[str]] = {path: set() for path in event_paths(home)}  # event path -> its categories
        cities: dict[str, str] = {}  # event path -> the city list it is on
        for path in lists:
            page = await self.optional(f"list {path}", self.get_text(s.site_url + path))
            label = category_of(path, s.category_prefixes)
            for event_path in event_paths(page or ""):
                found.setdefault(event_path, set()).update([label] if label else [])
                if city := s.city_lists.get(path.rstrip("/")):
                    cities.setdefault(event_path, city)
        sitemap = await self.optional("events sitemap", self.get_text(s.site_url + s.events_sitemap))
        for event_path in sitemap_paths(sitemap, ["/event/"]):
            found.setdefault(event_path, set())
        paths = list(found)
        self.log.debug("listed %d events on %d lists", len(paths), len(lists))

        async def fetch(path: str) -> ScrapedEvent | None:
            event = parse_event(await self.get_text(s.site_url + path), s.site_url + path, sorted(found[path]), towns)
            if event and not event.city and (city := cities.get(path)):
                event.city = city
            return event

        # The lists give no date: the home page's order (soonest first) leads, as the site shows them.
        async for event in self.stream_isolated(paths, fetch, label=lambda p: p):
            if is_current(
                event.start_at, event.end_at, RetentionSettings(), self.timezone, time_known=event.time_known
            ):
                yield event


def listing_paths(home_html: str, prefixes: list[str]) -> list[str]:
    """The lists the home page links (in its order, each once)."""
    paths = (unquote(p) for p in re.findall(r'href="(/[^"#?]+)"', home_html))
    return list(dict.fromkeys(p for p in paths if any(p.startswith(x) for x in prefixes)))


def sitemap_paths(xml: str | None, prefixes: list[str]) -> list[str]:
    """A sitemap's pages on this site whose path starts with one of ``prefixes`` (in its order, each once)."""
    paths = (unquote(urlsplit(loc.strip()).path) for loc in re.findall(r"<loc>([^<]+)</loc>", xml or ""))
    return list(dict.fromkeys(p for p in paths if any(p.startswith(x) for x in prefixes)))


def event_paths(page_html: str) -> list[str]:
    return list(dict.fromkeys(unquote(p) for p in _EVENT_LINK.findall(page_html)))


def category_of(path: str, prefixes: list[str]) -> str | None:
    """A genre / audience list's category: its slug in words ("/genre/techno-music" -> "techno music")."""
    prefix = next((x for x in prefixes if path.startswith(x)), None)
    slug = path[len(prefix) :].strip("/") if prefix else ""
    return slug.replace("-", " ").lower() or None


def _town_names() -> set[str]:
    """Every configured city's names, casefolded: a location that is only a town is the city, not a venue."""
    from partylinks.config import get_settings  # local: the module stays importable without settings

    cities = get_settings().enrichment.cities
    return {n.casefold() for c in cities for n in (c.he, c.en, *c.aliases) if n}


def split_location(text: str | None, towns: set[str] | None = None) -> tuple[str | None, str | None, str | None]:
    """ "<venue>, <street>, <city>, ישראל" -> (venue, address, city). Two parts: venue (or a street) and city.

    A street ("שדרות רוטשילד 24", a number in it) is the address, not a venue, and a town, region or country
    alone ("הרצליה", "Tel Aviv", "מחוז הצפון", "Israël") is the city: as venues they made a venue page each
    and split one bar over two (production, 23.11.2026). ``towns``: casefolded town names (``_town_names``)."""
    towns = towns if towns is not None else set()
    parts = [p for part in (text or "").split(",") if (p := part.strip())]
    while parts and (parts[-1].casefold() in _COUNTRY or parts[-1].casefold() in _NOT_VENUES):
        parts = parts[:-1]
    if not parts:
        return None, None, None
    if len(parts) == 1:
        only = parts[0]
        if only.casefold() in towns:
            return None, None, only
        return (None, only, None) if any(c.isdigit() for c in only) else (only, None, None)
    venue, city = parts[0], parts[-1]
    street = ", ".join(parts[1:-1]) or None
    if any(c.isdigit() for c in venue) or venue.casefold() in towns:  # a street (or the town again), not a venue
        return None, street or venue, city
    return venue, street, city


def _seller(url: Any) -> str | None:
    """The offer's seller page without its query (Go-Out's ``ref`` affiliate code)."""
    text = clean_str(url)
    if not text or not text.startswith("http"):
        return None
    parts = urlsplit(text)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def parse_event(page_html: str, url: str, categories: list[str], towns: set[str] | None = None) -> ScrapedEvent | None:
    """An event page -> its event (from the JSON-LD ``Event``); None without one, a name or a start, or cancelled."""
    node = next((n for n in jsonld_nodes(page_html) if n.get("@type") == "Event"), None)
    if node is None or "Cancelled" in str(node.get("eventStatus") or ""):
        return None
    title, start_at = clean_str(node.get("name")), parse_iso(node.get("startDate"), "Asia/Jerusalem")
    if not title or start_at is None:
        return None
    location = node.get("location") if isinstance(node.get("location"), dict) else {}
    address = location.get("address") if isinstance(location.get("address"), dict) else {}
    venue, street, city = split_location(
        clean_str(location.get("name")) or clean_str(address.get("streetAddress")), towns
    )
    offer = node.get("offers")
    offer = (offer[0] if offer else {}) if isinstance(offer, list) else offer if isinstance(offer, dict) else {}
    price = to_decimal(offer.get("price"))
    images = node.get("image")
    images = [images] if isinstance(images, str) else images if isinstance(images, list) else []
    seller = _seller(offer.get("url"))
    return ScrapedEvent(
        source=Parties247Scraper.name,
        source_event_id=url.rstrip("/").rsplit("/event/", 1)[-1],
        url=url,
        title=title,
        description=html_to_text(clean_str(node.get("description"))),
        start_at=start_at,
        venue_name=venue,
        address=street,
        city=city,
        source_genres=categories,
        images=[i for i in images if isinstance(i, str) and i.startswith("http")],
        min_price=price,
        max_price=price,
        is_sold_out="SoldOut" in str(offer.get("availability") or ""),
        extra={"purchase_url": seller} if seller else {},
    )
