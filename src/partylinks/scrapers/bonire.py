"""Bonire (bonire.live) - an Israeli events guide (Next.js, he / en / ru) that gathers shows, concerts,
parties, lectures, exhibitions and cinema screenings from the box offices (Eventer, Go-Out, muzi,
Kupat, Smarticket, the cinema chains...) and links each date to its seller.

Read in three passes, nearest first inside each:

1. The site's own "load more" JSON: ``/api/afisha/category-grid?category=<c>&offset=&limit=`` for each
   category (music, parties, theater, exhibitions, stand-up, classic, ballet, talks, kids, festivals),
   ``nextOffset`` / ``hasMore`` paging to the end. Each item is an event (or a show with its
   ``occurrences``: one event per date), with the venue (Hebrew name, address, city, coordinates), the
   site's genres, the event type, prices, the seller link (``ticketUrl`` / ``sourcePostUrl``), the
   artists and ``/api/images/...`` artwork. ~17,000 dates over ~170 requests (26.11.2026). Rows that
   already started today are no longer in the grid. The ``lang`` parameter filters by the event's
   language, so it is not sent. ``cinema`` answers 400: screenings come from pass 2.
2. The cinema category: every movie page (``/sitemap/movies.xml``) carries its screenings as JSON-LD
   ``ScreeningEvent`` nodes (cinema, time, the chain's order link).
3. Every slug the grid does not list: the event ids named on the festival, organiser, venue and artist
   pages (their sitemaps) and on the city cinema pages (``/sitemap/listings.xml``), and the event
   sitemaps' own ids. Each id not read yet is read from its event page's JSON-LD (festival and
   organiser screenings, film-club nights: ~40 of 10 festival pages on 26.11.2026).

Event page URLs: a show's page (``/he/show/<slug>-<id>``), else the event sitemap's URL, else
``/he/event/e-<id>`` (the site redirects any slug to the canonical page). The JSON-LD times are wall
clock in Israel; some carry a broken offset and hour 24 ("2026-11-27T24:20:00+26:00"), read as 00:20
of the next day.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Iterable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import urljoin

from pydantic import Field

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, jsonld_nodes, parse_iso, schema_types, to_decimal, to_float, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_EVENT_LINK = re.compile(r"/event/[a-z0-9-]*?(" + _UUID + r")")
_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_WALL_CLOCK = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?:T(\d{1,2}):(\d{2}))?")


class BonireSettings(SourceSettings):
    site_url: str = "https://bonire.live"
    locale: str = "he"
    grid_path: str = "/api/afisha/category-grid?category={category}&offset={offset}&limit={limit}"
    page_size: int = 100
    sitemap_path: str = "/sitemap.xml"
    event_page_path: str = "/he/event/e-{id}"
    show_page_path: str = "/he/show/{slug}-{public_id}"
    # The grid's categories and the label each one adds to an event (the grid's own ``category`` is
    # only music / theater / talks...: a party is "music", a children's play "theater").
    categories: dict[str, str] = Field(
        default_factory=lambda: {
            "music": "מוזיקה",
            "parties": "מסיבות",
            "theater": "תיאטרון",
            "exhibitions": "תערוכות",
            "stand-up": "סטנדאפ",
            "classic": "מוזיקה קלאסית",
            "ballet": "מחול",
            "talks": "הרצאות",
            "kids": "ילדים",
            "festivals": "פסטיבלים",
        }
    )
    # Breadcrumb names that are no category (the site's name, the home page).
    crumbs_skipped: list[str] = Field(default_factory=lambda: ["bonire", "ראשי", "home"])
    # Sub-sitemaps whose pages name event ids (pass 3), and the ones listing events themselves.
    page_sitemaps: list[str] = Field(default_factory=lambda: ["festivals", "organizers", "venues", "artists"])
    event_sitemap_prefix: str = "events-"
    movie_sitemap: str = "movies"
    listing_sitemap: str = "listings"
    # City listing pages read for event links (the other categories are the grid's rows).
    listing_categories: list[str] = Field(default_factory=lambda: ["cinema"])
    # JSON-LD @type -> label (pass 3 pages carry no category of their own).
    type_labels: dict[str, str] = Field(
        default_factory=lambda: {
            "musicevent": "מוזיקה",
            "theaterevent": "תיאטרון",
            "screeningevent": "קולנוע",
            "comedyevent": "סטנדאפ",
            "danceevent": "מחול",
            "exhibitionevent": "תערוכות",
            "educationevent": "הרצאות",
            "childrensevent": "ילדים",
            "festival": "פסטיבלים",
        }
    )


class BonireScraper(BaseScraper[BonireSettings]):
    name = "bonire"
    settings_model = BonireSettings

    def default_headers(self) -> dict[str, str]:
        return {"Accept": "application/json, text/html;q=0.9"}

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        sitemaps = await self._sitemaps()
        event_urls = {
            m.group(1): locale_url(u, s)
            for u in _urls_of(sitemaps, s.event_sitemap_prefix)
            if (m := re.search(f"({_UUID})$", u))
        }
        seen: set[str] = set()

        # 1. The grid, every category to its last page.
        rows: dict[str, tuple[dict[str, Any], set[str]]] = {}
        async for category, items in self.stream_isolated(s.categories, self._grid, lambda c: f"category {c}"):
            for item in items:
                key = str(item.get("id"))
                rows.setdefault(key, (item, set()))[1].add(category)
        for row, _ in rows.values():
            seen.update(str(o.get("id")) for o in [row, *(row.get("occurrences") or [])] if isinstance(o, dict))
        events = [e for row, cats in rows.values() for e in grid_events(row, cats, event_urls, s, self.timezone)]
        for event in sorted(events, key=_start):
            if self._current(event):
                yield event
        self.log.debug("grid: %d rows, %d dates", len(rows), len(events))

        # 2. Cinema: every movie page's screenings.
        movies = [locale_url(u, s) for u in sitemaps.get(s.movie_sitemap, [])]
        linked: list[str] = []
        async for page_url, html in self.stream_isolated(movies, self._page, lambda u: f"movie {u}"):
            linked.extend(_EVENT_LINK.findall(html))
            for event in sorted(page_events(html, page_url, s, self.timezone), key=_start):
                if event.source_event_id not in seen and self._current(event):
                    seen.add(event.source_event_id)
                    yield event

        # 3. Every other slug: ids named on the festival / organiser / venue / artist / city cinema pages.
        pages = [locale_url(u, s) for name in s.page_sitemaps for u in sitemaps.get(name, [])]
        pages += [
            locale_url(u, s)
            for u in sitemaps.get(s.listing_sitemap, [])
            if u.rstrip("/").rsplit("/", 1)[-1] in s.listing_categories
        ]
        async for _url, html in self.stream_isolated(dict.fromkeys(pages), self._page, lambda u: f"page {u}"):
            linked.extend(_EVENT_LINK.findall(html))
        missing = [i for i in dict.fromkeys([*linked, *event_urls]) if i not in seen]
        self.log.debug("pages: %d read, %d event ids not in the grid", len(pages), len(missing))

        async def read(event_id: str) -> list[ScrapedEvent]:
            url = event_urls.get(event_id) or s.site_url + s.event_page_path.format(id=event_id)
            _, html = await self._page(url)
            return [e for e in page_events(html, url, s, self.timezone) if self._current(e)]

        async for found in self.stream_isolated(missing, read, lambda i: f"event {i}"):
            for event in found:
                if event.source_event_id not in seen:
                    seen.add(event.source_event_id)
                    yield event

    def _current(self, event: ScrapedEvent) -> bool:
        return is_current(event.start_at, event.end_at, RetentionSettings(), self.timezone, time_known=event.time_known)

    async def _sitemaps(self) -> dict[str, list[str]]:
        """Each sub-sitemap's name ("events-music", "movies"...) -> the URLs it lists (one per page:
        the locale copies of one page are folded by ``locale_url`` later)."""
        s = self.settings
        index = await self.get_text(s.site_url + s.sitemap_path)
        out: dict[str, list[str]] = {}
        for sub in _LOC.findall(index):
            name = sub.rstrip("/").rsplit("/", 1)[-1].removesuffix(".xml")
            text = await self.optional(f"sitemap {name}", self.get_text(sub))
            out[name] = list(dict.fromkeys(locale_url(u, s) for u in _LOC.findall(text or "")))
        return out

    async def _grid(self, category: str) -> tuple[str, list[dict[str, Any]]]:
        s = self.settings
        items: list[dict[str, Any]] = []
        offset: int | None = 0
        while offset is not None:
            url = s.site_url + s.grid_path.format(category=category, offset=offset, limit=s.page_size)
            page = await self.get_json(url, expect=dict)
            items.extend(
                i["data"] for i in page.get("items") or [] if isinstance(i, dict) and isinstance(i.get("data"), dict)
            )
            next_offset = page.get("nextOffset")
            more = page.get("hasMore") and isinstance(next_offset, int) and next_offset > offset
            offset = next_offset if more and isinstance(next_offset, int) else None
        return category, items

    async def _page(self, url: str) -> tuple[str, str]:
        return url, await self.get_text(url)


def _urls_of(sitemaps: dict[str, list[str]], prefix: str) -> list[str]:
    return [u for name, urls in sitemaps.items() if name.startswith(prefix) for u in urls]


def _start(event: ScrapedEvent) -> datetime:
    return event.start_at or datetime.max.replace(tzinfo=zone("UTC"))


def locale_url(url: str, s: BonireSettings) -> str:
    """A page in the site's Hebrew copy: ``/artist/x`` and ``/ru/artist/x`` -> ``/he/artist/x``."""
    path = re.sub(r"^https?://[^/]+", "", url)
    path = re.sub(r"^/(?:he|en|ru|ar)(?=/|$)", "", path) or "/"
    return f"{s.site_url}/{s.locale}{path if path != '/' else ''}"


def _he(i18n: Any, fallback: Any) -> str | None:
    if isinstance(i18n, dict):
        text = clean_str(i18n.get("he"))
        if text:
            return text
    return clean_str(fallback)


def _price(value: Any) -> Decimal | None:
    number = to_decimal(value)
    return number if number is not None and number >= 0 else None


def grid_events(
    row: dict[str, Any], categories: Iterable[str], event_urls: dict[str, str], s: BonireSettings, timezone: str
) -> list[ScrapedEvent]:
    """One grid item -> one event per date (a show's ``occurrences``, else the item itself)."""
    title = _he(row.get("titleI18n"), row.get("title"))
    if not title:
        return []
    venue = row.get("venue") if isinstance(row.get("venue"), dict) else {}
    hidden = bool(venue.get("isUndisclosed"))
    lat, lon = (None, None) if hidden else (to_float(venue.get("lat")), to_float(venue.get("lng")))
    labels = [s.categories[c] for c in categories if c in s.categories]
    genres = [g for g in row.get("genres") or [] if isinstance(g, dict)]
    source_genres = list(
        dict.fromkeys(
            x.lower()
            for x in [
                *labels,
                *(clean_str(g.get("nameHe")) for g in genres),
                *(clean_str(g.get("slug")) for g in genres),
                clean_str(row.get("eventType")),
            ]
            if x
        )
    )
    artists = [
        n for n in (_he(a.get("nameI18n"), a.get("name")) for a in row.get("artists") or [] if isinstance(a, dict)) if n
    ]
    images = [urljoin(s.site_url, p) for p in row.get("imageUrls") or [] if isinstance(p, str) and "_thumb" not in p]
    route = row.get("showRoute") if isinstance(row.get("showRoute"), dict) else None
    show_url = (
        s.site_url + s.show_page_path.format(slug=route["slug"], public_id=route["publicId"])
        if route and route.get("slug") and route.get("publicId")
        else None
    )
    dates = [o for o in row.get("occurrences") or [] if isinstance(o, dict) and o.get("id")] or [row]
    out = []
    for occ in dates:
        merged = {**row, **occ}
        tz = clean_str(merged.get("eventTimezone")) or timezone
        start = parse_iso(merged.get("eventDate"), "UTC")
        if start is None:
            continue
        start = start.astimezone(zone(tz))
        end = parse_iso(row.get("endDate"), "UTC") if len(dates) == 1 else None
        end = end.astimezone(zone(tz)) if end and end > start else None
        occ_id = str(occ["id"])
        url = show_url or event_urls.get(occ_id) or s.site_url + s.event_page_path.format(id=occ_id)
        low, high = _price(merged.get("priceMinAmount")), _price(merged.get("priceMaxAmount"))
        free = bool(merged.get("priceIsFree"))
        seller = clean_str(merged.get("ticketUrl")) or clean_str(merged.get("sourcePostUrl"))
        out.append(
            ScrapedEvent(
                source=BonireScraper.name,
                source_event_id=occ_id,
                url=url,
                title=title,
                start_at=start,
                end_at=end,
                time_known=bool(clean_str(merged.get("eventTime"))),
                venue_name=_he(venue.get("nameI18n"), venue.get("name")),
                address=None if hidden else _he(venue.get("addressI18n"), venue.get("address")),
                city=clean_str(venue.get("cityNameHe")) or clean_str(venue.get("cityNameEn")),
                latitude=lat if lat is not None and lon is not None else None,
                longitude=lon if lat is not None and lon is not None else None,
                source_genres=source_genres,
                artists=artists,
                organizer=_he(row.get("productionCompanyI18n"), row.get("productionCompany")),
                images=images,
                min_price=Decimal(0) if free else low,
                max_price=Decimal(0) if free else (high if high is not None else low),
                currency=clean_str(merged.get("priceCurrency")) or "ILS",
                is_sold_out=bool(merged.get("isSoldOut")),
                extra={"purchase_url": seller or url},
            )
        )
    return out


def wall_clock(value: Any, timezone: str) -> tuple[datetime | None, bool]:
    """A JSON-LD date as Israeli wall-clock time, and whether it has a time. Hour 24 is the next day's
    hour 0 (the site writes "2026-11-27T24:20:00+26:00" for 00:20 on the 28th); the offset is ignored."""
    m = _WALL_CLOCK.match(str(value or "").strip())
    if not m:
        return None, False
    year, month, day, hour, minute = m.groups()
    try:
        base = datetime(int(year), int(month), int(day), tzinfo=zone(timezone))
    except ValueError:
        return None, False
    if hour is None:
        return base, False
    return base + timedelta(hours=int(hour), minutes=int(minute)), True


def _offers(node: dict[str, Any]) -> list[dict[str, Any]]:
    raw = node.get("offers")
    return [o for o in (raw if isinstance(raw, list) else [raw]) if isinstance(o, dict)]


def page_events(html: str, page_url: str, s: BonireSettings, timezone: str) -> list[ScrapedEvent]:
    """The events of an event or movie page's JSON-LD (an event page's own node, a movie page's
    ``ScreeningEvent`` nodes)."""
    nodes = jsonld_nodes(html)
    titles = {str(n.get("@id")): clean_str(n.get("name")) for n in nodes if "movie" in schema_types(n)}
    crumbs = [
        name.lower()
        for n in nodes
        if "listitem" in schema_types(n) and (name := clean_str(n.get("name"))) and name.lower() not in s.crumbs_skipped
    ]
    out = []
    for node in nodes:
        types = schema_types(node)
        if not any(t.endswith("event") or t == "festival" for t in types) or "@id" not in node:
            continue
        m = re.search(f"#(?:event|screening)-({_UUID})$", str(node.get("@id")))
        title = clean_str(node.get("name"))
        start, timed = wall_clock(node.get("startDate"), timezone)
        if not m or not title or start is None:
            continue
        end, _ = wall_clock(node.get("endDate"), timezone)
        place = node.get("location") if isinstance(node.get("location"), dict) else {}
        address = place.get("address") if isinstance(place.get("address"), dict) else {}
        geo = place.get("geo") if isinstance(place.get("geo"), dict) else {}
        lat, lon = to_float(geo.get("latitude")), to_float(geo.get("longitude"))
        offers = _offers(node)
        prices = sorted(p for p in (_price(o.get("price")) for o in offers) if p is not None)
        for o in offers:
            spec = o.get("priceSpecification") if isinstance(o.get("priceSpecification"), dict) else {}
            prices += [p for p in (_price(spec.get("minPrice")), _price(spec.get("maxPrice"))) if p is not None]
        prices.sort()
        seller = next((clean_str(o.get("url")) for o in offers if clean_str(o.get("url"))), None)
        images = node.get("image")
        images = [
            i for i in (images if isinstance(images, list) else [images]) if isinstance(i, str) and i.startswith("http")
        ]
        performers = node.get("performer")
        performers = performers if isinstance(performers, list) else [performers]
        work = node.get("workPresented") if isinstance(node.get("workPresented"), dict) else {}
        url = clean_str(node.get("url")) or page_url
        labels = [s.type_labels[t] for t in types if t in s.type_labels] + crumbs
        out.append(
            ScrapedEvent(
                source=BonireScraper.name,
                source_event_id=m.group(1),
                url=url.split("?")[0] if "screeningevent" in types else url,
                title=titles.get(str(work.get("@id"))) or title,
                description=clean_str(node.get("description")),
                start_at=start,
                end_at=end if timed and end and end > start else None,
                time_known=timed,
                venue_name=clean_str(place.get("name")),
                address=clean_str(address.get("streetAddress")),
                city=clean_str(address.get("addressLocality")),
                latitude=lat if lat is not None and lon is not None else None,
                longitude=lon if lat is not None and lon is not None else None,
                source_genres=list(dict.fromkeys(x for x in labels if x)),
                artists=[n for n in (clean_str(p.get("name")) for p in performers if isinstance(p, dict)) if n],
                images=images,
                min_price=prices[0] if prices else None,
                max_price=prices[-1] if prices else None,
                extra={"purchase_url": seller or url},
            )
        )
    return out
