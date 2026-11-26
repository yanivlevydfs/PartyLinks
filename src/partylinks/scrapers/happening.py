"""StayHappening (stayhappening.com) and HappeningNext (happeningnext.com) - two English city guides
that mirror the allevents.in catalogue (the same events, the same banners) under their own URLs.
One reader, two sources: each site's pages and links are its own.

No sitemap, no API, no city index: a city has a page (``/tel+aviv``; an unknown one answers 404) and
category pages (stayhappening ``/tel+aviv--music``, happeningnext ``/tel+aviv/music``), each with a
JSON-LD list of its events (title, date without time, venue, coordinates, country). The cities are a
configured list of Israeli towns plus every town an event page names. A stayhappening city page shows
at most 100 events (Tel Aviv: 100 of ~160), so a city at that cap is read by category too: the
categories its page links, the configured allevents vocabulary and every tag an event page names, to
the last new one. A happeningnext city page is a few sliders (27 of ~140 in Tel Aviv), so every
category is read for every city. Only events in Israel (``addressCountry`` IL) are kept.

Each event's page adds the time and zone, the description, the organiser, its tags (category slugs)
and, on stayhappening, the seller link (base64 in the "Find tickets" button: bandsintown, eventbrite,
the box office). happeningnext's button opens the allevents app: no seller link there.
"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import AsyncIterator
from typing import Any, ClassVar
from urllib.parse import quote, unquote

import httpx
from pydantic import Field

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, html_to_text, jsonld_nodes, parse_iso, schema_types, to_float, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_DATA_LINK = re.compile(r"""data-link=["']([^"']+)["']""")


class HappeningSettings(SourceSettings):
    site_url: str = "https://stayhappening.com"
    city_path: str = "/{city}"
    category_path: str = "/{city}--{category}"
    # Path of an event page (to tell event links from city and category links).
    event_marker: str = "/e/"
    # A city page holding this many events is cut: read its categories too (0: always read them).
    city_page_cap: int = 100
    country: str = "IL"
    # Israeli towns the site may have a page for (404 for the others; each run tries them all).
    cities: list[str] = Field(default_factory=lambda: ["tel aviv", "jerusalem", "haifa"])
    # The allevents category slugs (the city pages link only a few of them).
    categories: list[str] = Field(default_factory=lambda: ["music", "concerts", "parties"])
    # The site's own pages, no town (happeningnext /p/cities, /p/contact: a US index, the footer).
    site_pages: list[str] = Field(default_factory=lambda: ["/p/"])
    # Seller links that are no seller (the allevents app stores).
    skip_link_hosts: list[str] = Field(
        default_factory=lambda: ["play.google.com", "apps.apple.com", "itunes.apple.com"]
    )


class _HappeningScraper(BaseScraper[HappeningSettings]):
    settings_model = HappeningSettings
    # A category page's path after the city: "--music" (stayhappening), "/music" (happeningnext).
    tag_pattern: ClassVar[str] = r"--([a-z0-9-]+)"

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        listed: dict[str, tuple[dict[str, Any], set[str]]] = {}
        tried: set[str] = set()
        read_categories: dict[str, set[str]] = {}
        cities = list(dict.fromkeys(slug(c) for c in s.cities))
        tags: dict[str, set[str]] = {}

        while True:
            # Cities not read yet: their page, and their categories when the page is cut.
            todo = [c for c in cities if c not in tried]
            tried.update(todo)
            async for city, html in self.stream_isolated(todo, self._city, lambda c: f"city {c}"):
                if html is None:
                    continue
                rows = listing_rows(html, s)
                for row in rows:
                    listed.setdefault(row["url"], (row, set()))
                if not s.city_page_cap or len(rows) >= s.city_page_cap:
                    tags.setdefault(city, set()).update({*s.categories, *page_tags(html, s, self.tag_pattern)})
            pages = [(c, cat) for c, cats in tags.items() for cat in sorted(cats - read_categories.get(c, set()))]
            for c, cat in pages:
                read_categories.setdefault(c, set()).add(cat)
            async for (city, category), html in self.stream_isolated(pages, self._category, lambda p: f"category {p}"):
                for row in listing_rows(html or "", s):
                    listed.setdefault(row["url"], (row, set()))[1].add(category)

            # Events not read yet, soonest first: each page's time, tags (more categories) and towns.
            fresh = sorted(
                (u for u, (row, _) in listed.items() if not row.get("_read") and self._current_row(row)),
                key=lambda u: str(listed[u][0].get("startDate")),
            )
            async for event, found_tags in self.stream_isolated(
                fresh, lambda u: self._event(u, listed), lambda u: f"event {u}"
            ):
                town = slug(event.city or "")
                if town and town not in cities:
                    cities.append(town)
                if event.city and slug(event.city) in tags:
                    tags[slug(event.city)].update(found_tags)
                if is_current(
                    event.start_at, event.end_at, RetentionSettings(), self.timezone, time_known=event.time_known
                ):
                    yield event
            more_categories = any(cats - read_categories.get(c, set()) for c, cats in tags.items())
            if not fresh and not more_categories and all(c in tried for c in cities):
                break

    async def _city(self, city: str) -> tuple[str, str | None]:
        url = self.settings.site_url + self.settings.city_path.format(city=quote(city, safe="+"))
        return city, await self.optional_page(url)

    async def _category(self, page: tuple[str, str]) -> tuple[tuple[str, str], str | None]:
        city, category = page
        url = self.settings.site_url + self.settings.category_path.format(city=quote(city, safe="+"), category=category)
        return page, await self.optional_page(url)

    async def optional_page(self, url: str) -> str | None:
        """A page, or None when the site has none (404: a town or category it does not list)."""
        try:
            return (await self.request("GET", url)).text
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

    def _current_row(self, row: dict[str, Any]) -> bool:
        start = parse_iso(row.get("startDate"), self.timezone)
        end = parse_iso(row.get("endDate"), self.timezone)
        return start is not None and is_current(start, end, RetentionSettings(), self.timezone, time_known=False)

    async def _event(
        self, url: str, listed: dict[str, tuple[dict[str, Any], set[str]]]
    ) -> tuple[ScrapedEvent, set[str]] | None:
        row, categories = listed[url]
        row["_read"] = True
        html = await self.get_text(url)
        event = parse_event_page(html, row, categories, self.settings, self.name, self.timezone, self.tag_pattern)
        if event is None:
            return None
        return event, page_tags(html, self.settings, self.tag_pattern)


class StayHappeningScraper(_HappeningScraper):
    name = "stayhappening"


class HappeningNextScraper(_HappeningScraper):
    name = "happeningnext"
    tag_pattern: ClassVar[str] = r"/([a-z0-9-]+)"


def slug(city: str) -> str:
    """A town as the site writes it in a URL: "Tel Aviv" -> "tel+aviv"."""
    return "+".join(re.sub(r"[^\w\s'-]", " ", city.lower()).split())


def listing_rows(html: str, s: HappeningSettings) -> list[dict[str, Any]]:
    """A town or category page's JSON-LD events in the configured country."""
    out = []
    for node in jsonld_nodes(html):
        url = clean_str(node.get("url"))
        if not url or s.event_marker not in url or not any(t.endswith("event") for t in schema_types(node)):
            continue
        if country_of(node) not in (s.country, None):
            continue
        out.append(node)
    return out


def country_of(node: dict[str, Any]) -> str | None:
    place = node.get("location") if isinstance(node.get("location"), dict) else {}
    address = place.get("address") if isinstance(place.get("address"), dict) else {}
    return clean_str(address.get("addressCountry"))


def page_tags(html: str, s: HappeningSettings, tag_pattern: str) -> set[str]:
    """The category slugs a page links (``/tel+aviv--pop`` -> "pop"), not its event links."""
    category = re.compile(r"/[a-z0-9+%'-]+" + tag_pattern)
    found = set()
    for href in re.findall(r"""href=["']([^"']+)["']""", html):
        path = href[len(s.site_url) :]
        if not href.startswith(s.site_url + "/") or s.event_marker in href or path.startswith(tuple(s.site_pages)):
            continue
        m = category.fullmatch(path)
        if m:
            found.add(m.group(1))
    return found


def seller_link(html: str, s: HappeningSettings) -> str | None:
    """The "Find tickets" button's target (base64 in ``data-link``), not an app-store link."""
    for raw in _DATA_LINK.findall(html):
        link = raw
        if not raw.startswith("http"):
            try:
                link = base64.b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8")
            except (binascii.Error, UnicodeDecodeError, ValueError):
                continue
        if link.startswith("http") and not any(h in link for h in s.skip_link_hosts):
            return link
    return None


def parse_event_page(
    html: str,
    row: dict[str, Any],
    categories: set[str],
    s: HappeningSettings,
    source: str,
    timezone: str,
    tag_pattern: str,
) -> ScrapedEvent | None:
    """An event page's JSON-LD (time, zone, description), over its listing row."""
    url = str(row["url"])
    node = next(
        (
            n
            for n in jsonld_nodes(html)
            if clean_str(n.get("url")) == url and any(t.endswith("event") for t in schema_types(n))
        ),
        row,
    )
    if country_of(node) not in (s.country, None):
        return None
    title = clean_str(html_to_text(clean_str(node.get("name")) or clean_str(row.get("name"))))
    raw_start = clean_str(node.get("startDate")) or clean_str(row.get("startDate"))
    timed = bool(raw_start and "T" in raw_start)
    start = parse_iso(raw_start, timezone)
    if not title or start is None:
        return None
    start = start.astimezone(zone(timezone))
    end = parse_iso(node.get("endDate"), timezone)
    end = end.astimezone(zone(timezone)) if timed and end and end > start else None
    place = node.get("location") if isinstance(node.get("location"), dict) else {}
    address = place.get("address") if isinstance(place.get("address"), dict) else {}
    geo = place.get("geo") if isinstance(place.get("geo"), dict) else {}
    lat, lon = to_float(geo.get("latitude")), to_float(geo.get("longitude"))
    venue = clean_str(html_to_text(clean_str(place.get("name"))))
    street = clean_str(address.get("streetAddress"))
    images = node.get("image")
    images = [
        i for i in (images if isinstance(images, list) else [images]) if isinstance(i, str) and i.startswith("http")
    ]
    organizers = node.get("organizer")
    organizers = organizers if isinstance(organizers, list) else [organizers]
    organizer = next(
        (clean_str(o.get("name")) for o in organizers if isinstance(o, dict) and clean_str(o.get("name"))), None
    )
    genres = sorted({*categories, *page_tags(html, s, tag_pattern)})
    seller = seller_link(html, s)
    event_id = unquote(url.rstrip("/").rsplit("-", 1)[-1])
    return ScrapedEvent(
        source=source,
        source_event_id=event_id,
        url=url,
        title=title,
        description=clean_str(html_to_text(clean_str(node.get("description")))),
        start_at=start,
        end_at=end,
        time_known=timed,
        venue_name=venue,
        address=street if street and street != venue else None,
        city=clean_str(address.get("addressLocality")),
        latitude=lat if lat is not None and lon is not None and (lat, lon) != (0.0, 0.0) else None,
        longitude=lon if lat is not None and lon is not None and (lat, lon) != (0.0, 0.0) else None,
        source_genres=[g.replace("-", " ") for g in genres],
        organizer=organizer,
        images=images,
        extra={"purchase_url": seller or url},
    )
