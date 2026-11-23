"""Tickchak LIVE (live.tickchak.co.il) - an aggregator across ticketing providers.

The category/city page's ``__NEXT_DATA__`` describes the page layout: which
event sliders exist (``pageDesign.sections`` + ``sectionsData.loopSections``)
and the breadcrumb hierarchy that scopes them. Each slider is then paginated via
``GET api-live/sectionData/<sectionId>?offset&limit&pageHierarchy[..]`` until the
reported ``count`` is reached, plus ``/customEventsNew``. Nothing about the
sections is hard-coded; they are discovered from the page every run.
Slider items already carry venue, artists, price range and provider link, so no
per-event request is needed. Ticket tiers live on the upstream provider; an item without a price
("0 / 0") gets the tiers its provider's page states, when it does (seller_page).
Events whose seller set no category are on no category page: the event sitemap adds them
(``tickchak_sitemap``, one page request each).
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from pydantic import Field

from partylinks.artists import ArtistProfile
from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import ParseError, clean_str, from_unix, is_site_logo, next_data, og_image, to_decimal
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceBlockedError, SourceSettings
from partylinks.scrapers.matnasnet import covered_smarticket_hosts
from partylinks.scrapers.seller_page import fetch_seller_page, seller_tiers
from partylinks.scrapers.tickchak_sitemap import unlisted_items

SLIDER_SECTION = "eventSlider"
LOOP_SECTION = "loopSections"
ONLINE_WORDS = ("zoom", "בזום")


class TickchakSettings(SourceSettings):
    site_url: str
    api_url: str
    category_slug: str = ""
    # Several category pages instead of one: slug -> the label tagged on its events, which the
    # enrichment's declared_genres turn into the site's category ("standup" -> "stand-up" -> stand_up).
    categories: dict[str, str] = Field(default_factory=dict)
    # New sellers' pages read for artwork per run (the rest on later runs; found artwork is kept).
    max_artwork_lookups: int = Field(300, ge=0)
    city_slug: str = ""  # empty = the category page for all of Israel
    section_data_path: str
    custom_events_path: str
    page_size: int = Field(20, ge=1)
    max_pages: int = Field(50, ge=1)
    # Events on no category page, read from the event sitemap (tickchak_sitemap). Empty = off.
    event_sitemap_path: str = ""
    # The label of sitemap events whose seller set no category (as `categories` labels a page's events).
    uncategorized_label: str = ""


class TickchakScraper(BaseScraper[TickchakSettings]):
    name = "tickchak"
    settings_model = TickchakSettings

    def default_headers(self) -> dict[str, str]:
        return {"Origin": self.settings.site_url, "Referer": self.settings.site_url + "/"}

    # Whether a page's preloaded events and its loop sliders count. On a venue page both hold
    # other venues' events (the loops are city-wide), so ArenasScraper keeps only its event sliders.
    use_preloaded = True
    use_loops = True

    def page_paths(self) -> list[str]:
        s = self.settings
        return [f"{slug}/{s.city_slug}".rstrip("/") for slug in (s.categories or [s.category_slug])]

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        items: dict[Any, dict[str, Any]] = {}
        labels: dict[Any, set[str]] = {}
        loaded = False
        for path in self.page_paths():
            label = s.categories.get(path.split("/", 1)[0])
            # One page failing (a renamed slug) never costs the others; at least one must load.
            found = await self.optional(f"page {path}", self._page_items(path))
            loaded = loaded or found is not None
            for entity_id, item in (found or {}).items():
                items.setdefault(entity_id, item)
                if label:
                    labels.setdefault(entity_id, set()).add(label)
        if not loaded:
            raise ParseError("no listing page loaded")
        self.log.debug("listed %d events", len(items))
        # Smarticket box offices the smarticket source reads are left to it: it has the artwork, and
        # Tickchak's copies can be stale (a removed show's link redirects to /404).
        skip = covered_smarticket_hosts()
        missing: list[tuple[ScrapedEvent, str]] = []  # no artwork here: the seller's page may have it
        # The category pages' events go out before the sitemap's ~320 page reads, soonest first (Nov 2026):
        # a run a deploy cuts there already holds every listed event.
        for event in self._events(items, labels, skip, missing):
            yield event
        if s.event_sitemap_path:
            unlisted: dict[Any, dict[str, Any]] = {}
            sitemap = unlisted_items(self, s.site_url + s.event_sitemap_path, set(items))
            for item in await self.optional("event sitemap", sitemap) or []:
                slug = item.get("categorySlug")
                # A category we do not read (football) stays out; no category gets uncategorized_label.
                label = s.categories.get(slug) if slug else s.uncategorized_label
                if label is not None and item["entityId"] not in items:
                    unlisted.setdefault(item["entityId"], item)
                    if label:
                        labels.setdefault(item["entityId"], set()).add(label)
            self.log.debug("%d more events from the sitemap", len(unlisted))
            for event in self._events(unlisted, labels, skip, missing):
                yield event
        # In parallel (the client's concurrency bounds it), at most max_artwork_lookups new pages a run:
        # one by one, ~1,500 sellers' pages outlasted the 60-min run (Oct 2026). Found artwork is kept,
        # so the rest is read over the next runs. Shuffled: dead seller links (redirect to /404) are
        # never stored, so in listing order they would take the same slots every run. Unpriced ones first
        # (~100): a price is not remembered between runs as artwork is.
        random.shuffle(missing)
        missing.sort(key=lambda m: m[0].min_price is not None)
        looked_up = missing[: s.max_artwork_lookups]
        for event in soonest_first([e for e, _ in missing[s.max_artwork_lookups :]]):  # no lookup: out first
            yield event
        gate = asyncio.Semaphore(self.http.concurrency)  # own clients (fetch_seller_page): bound them here
        await asyncio.gather(*(self._provider_page(e, link, gate) for e, link in looked_up))
        for event in soonest_first([e for e, _ in looked_up]):
            yield event

    def _events(
        self, items: dict[Any, dict[str, Any]], labels: dict[Any, set[str]], skip: frozenset[str],
        missing: list[tuple[ScrapedEvent, str]],
    ) -> list[ScrapedEvent]:  # fmt: skip
        """The current events of ``items``, soonest first; those that need their seller's page go to ``missing``."""
        ready: list[ScrapedEvent] = []
        for entity_id, item in items.items():
            if urlsplit(clean_str(item.get("link")) or "").hostname in skip or is_online(item):
                continue
            try:
                event = parse_event(item, self.settings.site_url, self.name)
            except Exception as exc:  # noqa: BLE001 - isolate per-item failures
                self.record_failure(str(entity_id), exc)
                continue
            # Today's shows stay listed after they end (and the sitemap lists them too): over is not imported.
            if not is_current(event.start_at, event.end_at, RetentionSettings(), self.timezone):
                continue
            event.event_types = sorted({*event.event_types, *labels.get(entity_id, ())})
            # Read on an earlier run; a site logo stored then (Ticketmaster's favicon as a seller's og:image) is
            # none, and is looked up again (Moshe Peretz at Menora kept it for weeks, Nov 2026).
            known = next((u for u in self.known_images.get(event.source_event_id) or [] if not is_site_logo(u)), None)
            if not event.images and known:
                event.images, event.image_url = [known], known
            unpriced = event.min_price is None and not event.is_sold_out
            if (not event.images or unpriced) and (link := clean_str(item.get("link"))):
                missing.append((event, link))
            else:
                ready.append(event)
        return soonest_first(ready)

    async def _page_items(self, path: str) -> dict[Any, dict[str, Any]]:
        s = self.settings
        page_state = _initial_state(next_data(await self.get_text(f"{s.site_url}/{path}")))
        page = page_state.get("page") or {}
        hierarchy = _hierarchy_params(page.get("breadcrumbs") or [])

        items: dict[Any, dict[str, Any]] = {}
        preloaded = ((page_state.get("data") or {}).get("event") or {}).get("all") if self.use_preloaded else None
        for item in preloaded.values() if isinstance(preloaded, dict) else []:
            if isinstance(item, dict):
                items.setdefault(item.get("entityId"), item)

        listings = [
            self._paginate(s.api_url + s.section_data_path.format(section_id=sid), "events", hierarchy + _model(sid))
            for sid in discover_section_ids(page)
            if self.use_loops or "_" not in sid
        ]
        listings.append(self._paginate(s.api_url + s.custom_events_path, "customEvents", hierarchy))
        # Every slider in parallel; a failing slider is recorded and the others still count.
        for listing in await asyncio.gather(*(self.optional("slider", listing) for listing in listings)):
            for item in listing or []:
                if isinstance(item, dict):
                    items.setdefault(item.get("entityId"), item)

        items.pop(None, None)
        return items

    async def _provider_page(self, event: ScrapedEvent, link: str, gate: asyncio.Semaphore) -> None:
        """Listings copied from another box office carry no artwork, or no price ("0 / 0"), here. The event's
        own page here has its artists' photos (the listings' artists have none); the seller's page may have
        artwork too (og:image) and the price (JSON-LD offers on Tickchak's white labels and Go-Out: seller_page).
        Artwork is read once and reused on later runs; a missing price is looked up every run."""
        if not event.images and event.url:
            async with gate:
                own = await self._own_page_artwork(str(event.url))
            if own:
                event.images, event.image_url = own, own[0]
        if event.images and (event.min_price is not None or event.is_sold_out):
            return  # nothing left that the seller's page could add
        # Through a client of its own: a seller refusing us (429 / 403) moved Tickchak's own API to the next
        # proxy, three times a minute on 2026-11-10.
        async with gate:
            page = await fetch_seller_page(self, link)
        if page is None:
            return
        if not event.images and (image := og_image(page)) and not is_site_logo(image):
            event.images, event.image_url = [image], image
        if event.min_price is None and (tiers := seller_tiers(page, link)):
            event.tickets = tiers

    async def _own_page_artwork(self, url: str) -> list[str]:
        """The event page's pictures here; none when the page cannot be read (the seller's page is next)."""
        try:
            return own_page_artwork(await self.get_text(url))
        except SourceBlockedError:
            raise
        except Exception as exc:  # noqa: BLE001 - artwork only: the event is saved without it
            self.log.debug("event page %s: %s", url, exc)
            return []

    async def _paginate(self, url: str, key: str, params: list[tuple[str, Any]]) -> list[dict[str, Any]]:
        size = self.settings.page_size

        async def page(index: int) -> tuple[list[dict[str, Any]], int | None]:
            payload = await self.get_json(url, params=[("limit", size), ("offset", index * size), *params], expect=dict)
            return payload.get(key) or [], payload.get("count")

        return await self.paginate(page, size, self.settings.max_pages)


def is_online(item: dict[str, Any]) -> bool:
    """A Zoom session (venue "אירוע ב ZOOM", or "מפגש ב-Zoom" in the title): online only, not imported.
    Yaara Kedar's museum talks are listed both at the museum and on Zoom (Nov 2026)."""
    text = f"{item.get('title') or ''} {(item.get('venue') or {}).get('title') or ''}".casefold()
    return any(word in text for word in ONLINE_WORDS)


def soonest_first(events: list[ScrapedEvent]) -> list[ScrapedEvent]:
    """Events by start, ascending; one without a start last."""
    far = datetime.max.replace(tzinfo=UTC)
    return sorted(events, key=lambda e: e.start_at or far)


def _profile(artist: dict[str, Any], site_url: str) -> ArtistProfile | None:
    name = clean_str(artist.get("titleEn") or artist.get("title"))
    path = clean_str(artist.get("url"))
    if not name or not path:
        return None
    try:  # streaming links arrive as a JSON string
        music = json.loads(artist.get("music") or "{}")
    except ValueError:
        music = {}
    genres = [c.get("titleEn") or c.get("title") for c in (artist.get("categories") or {}).get("all") or []]
    return ArtistProfile(
        name=name,
        source=TickchakScraper.name,
        genre=clean_str(", ".join(g for g in genres if g)),
        spotify_url=clean_str(music.get("spotify")) if isinstance(music, dict) else None,
        profile_url=site_url + path if path.startswith("/") else path,
    )


def _initial_state(data: dict[str, Any]) -> dict[str, Any]:
    state = data.get("props", {}).get("pageProps", {}).get("initialState")
    if not state:
        raise ParseError("initialState missing")
    return state


def _model(section_id: str) -> list[tuple[str, Any]]:
    """Loop-section ids look like "5_thisWeek"; the suffix is sent as modelId."""
    return [("modelId", section_id.split("_", 1)[1])] if "_" in section_id else []


def _hierarchy_params(breadcrumbs: list[dict[str, Any]]) -> list[tuple[str, Any]]:
    params: list[tuple[str, Any]] = []
    crumbs = [c for c in breadcrumbs if isinstance(c, dict) and c.get("type") and c.get("id") is not None]
    for index, crumb in enumerate(crumbs):
        params += [(f"pageHierarchy[{index}][type]", crumb["type"]), (f"pageHierarchy[{index}][id]", crumb["id"])]
    return params


def discover_section_ids(page: dict[str, Any]) -> list[str]:
    """Event slider ids exactly as the site requests them (``"4"``, ``"5_thisWeek"``...)."""
    loops = (page.get("sectionsData") or {}).get(LOOP_SECTION) or {}
    ids: list[str] = []
    for section in (page.get("pageDesign") or {}).get("sections") or []:
        sid = str(section.get("sectionId"))
        if section.get("type") == SLIDER_SECTION:
            ids.append(sid)
        elif section.get("type") == LOOP_SECTION and section.get("content") == "event":
            sliders = (loops.get(sid) or {}).get("sliders") or []
            ids += [f"{sid}_{slider['id']}" for slider in sliders if isinstance(slider, dict) and slider.get("id")]
    return ids


def parse_event(item: dict[str, Any], site_url: str, source: str = "tickchak") -> ScrapedEvent:
    venue = item.get("venue") or {}
    artists = item.get("artists") or []
    categories = {
        c.get("titleEn") or c.get("title")
        for a in artists
        for c in (a.get("categories") or {}).get("all") or []
        if c.get("titleEn") or c.get("title")
    }
    priced = bool(to_decimal(item.get("maxPrice")))
    return ScrapedEvent(
        source=source,
        source_event_id=str(item["entityId"]),
        url=site_url + item["url"] if str(item.get("url", "")).startswith("/") else item.get("pageUrl"),
        title=clean_str(item.get("title")) or "",
        description=clean_str(item.get("description")),
        start_at=from_unix(item.get("date")),
        end_at=from_unix(item.get("endDate")),
        venue_name=clean_str(venue.get("title")),
        city=clean_str((venue.get("city") or {}).get("title")),
        event_types=sorted(categories),
        artists=[name for a in artists if (name := clean_str(a.get("titleEn") or a.get("title")))],
        artist_profiles=[p for a in artists if (p := _profile(a, site_url))],
        images=_artwork(item),
        # 0 / 0 is Tickchak's "no price known" (phone-only, sold out, the seller's own page), not free.
        min_price=to_decimal(item.get("minPrice")) if priced else None,
        max_price=to_decimal(item.get("maxPrice")) if priced else None,
        is_sold_out=bool(item.get("soldOut")),
        extra={
            "provider": item.get("provider"),
            "purchase_url": item.get("link"),
            "area": (venue.get("area") or {}).get("title"),
            "last_tickets": bool(item.get("lastTickets")),
            "pre_sale": bool(item.get("preSale")),
            "is_promoted": bool(item.get("isPromoted")),
        },
    )


def _artwork(item: dict[str, Any]) -> list[str]:
    """Default images are generic category illustrations, not the event's artwork."""
    image = item.get("image") or {}
    return [image["image"]] if image.get("image") and not image.get("isDefaultImage") else []


def own_page_artwork(page_html: str) -> list[str]:
    """An event page's pictures: the event's own (not the category illustration), else its artists' photos,
    which only the page carries (Moshe Peretz at Menora: a default illustration, and his photo on the page)."""
    event = _initial_state(next_data(page_html)).get("event") or {}
    photos = [
        image
        for artist in event.get("artists") or []
        if isinstance(artist, dict)
        and (image := clean_str((((artist.get("image") or {}).get("main")) or {}).get("image")))
    ]
    return [u for u in dict.fromkeys([*_artwork(event), *photos]) if not is_site_logo(u)]


class ArenasSettings(TickchakSettings):
    category_slug: str = ""
    # Tickchak LIVE venue pages (live.tickchak.co.il/<slug>), one per arena / stadium / amphitheater.
    venue_slugs: list[str]


class ArenasScraper(TickchakScraper):
    """Arenas, stadiums and amphitheaters: they have no box office of their own (each show is sold by
    its promoter on Eventim, Leaan, Ticketmaster...), so their Tickchak LIVE venue pages, which gather
    every seller's shows at that venue, are the one listing per venue."""

    name = "arenas"
    settings_model = ArenasSettings  # type: ignore[assignment]
    use_preloaded = False
    use_loops = False

    def page_paths(self) -> list[str]:
        return list(dict.fromkeys(self.settings.venue_slugs))  # type: ignore[attr-defined]
