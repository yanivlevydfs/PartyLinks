"""Tickchak LIVE (live.tickchak.co.il) - an aggregator across ticketing providers.

The category/city page's ``__NEXT_DATA__`` describes the page layout: which
event sliders exist (``pageDesign.sections`` + ``sectionsData.loopSections``)
and the breadcrumb hierarchy that scopes them. Each slider is then paginated via
``GET api-live/sectionData/<sectionId>?offset&limit&pageHierarchy[..]`` until the
reported ``count`` is reached, plus ``/customEventsNew``. Nothing about the
sections is hard-coded; they are discovered from the page every run.
Slider items already carry venue, artists, price range and provider link, so no
per-event request is needed. Ticket tiers live on the upstream provider.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlsplit

from pydantic import Field

from partylinks.artists import ArtistProfile
from partylinks.models import ScrapedEvent
from partylinks.parsing import ParseError, clean_str, from_unix, next_data, og_image, to_decimal
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.matnasnet import covered_smarticket_hosts

SLIDER_SECTION = "eventSlider"
LOOP_SECTION = "loopSections"


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
        for path in self.page_paths():
            label = s.categories.get(path.split("/", 1)[0])
            # One category failing (a renamed slug) never costs the others; the first one must load.
            found = await (self._page_items(path) if not items else self.optional(f"category {path}", self._page_items(path)))
            for entity_id, item in (found or {}).items():
                items.setdefault(entity_id, item)
                if label:
                    labels.setdefault(entity_id, set()).add(label)
        self.log.info("listed %d events", len(items))
        events: list[ScrapedEvent] = []
        missing: list[tuple[ScrapedEvent, str]] = []  # no artwork here: the seller's page may have it
        # Smarticket box offices the smarticket source reads are left to it: it has the artwork, and
        # Tickchak's copies can be stale (a removed show's link redirects to /404).
        skip = covered_smarticket_hosts()
        for entity_id, item in items.items():
            if urlsplit(clean_str(item.get("link")) or "").hostname in skip:
                continue
            try:
                event = parse_event(item, s.site_url, self.name)
            except Exception as exc:  # noqa: BLE001 - isolate per-item failures
                self.record_failure(str(entity_id), exc)
                continue
            event.event_types = sorted({*event.event_types, *labels.get(entity_id, ())})
            events.append(event)
            if not event.images and (link := clean_str(item.get("link"))):
                if known := (self.known_images.get(event.source_event_id) or [None])[0]:
                    event.images, event.image_url = [known], known  # read on an earlier run
                else:
                    missing.append((event, link))
        # In parallel (the client's concurrency bounds it), at most max_artwork_lookups new pages a run:
        # one by one, ~1,500 sellers' pages outlasted the 60-min run (Oct 2026). Found artwork is kept,
        # so the rest is read over the next runs. Shuffled: dead seller links (redirect to /404) are
        # never stored, so in listing order they would take the same slots every run.
        random.shuffle(missing)
        await asyncio.gather(*(self._provider_artwork(e, link) for e, link in missing[: s.max_artwork_lookups]))
        for event in events:
            yield event

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

    async def _provider_artwork(self, event: ScrapedEvent, link: str) -> None:
        """Listings copied from another box office carry no artwork here; its page has it.
        Read once: stored artwork is reused on later runs."""
        image = (self.known_images.get(event.source_event_id) or [None])[0]
        if image is None:
            try:
                image = og_image(await self.get_text(link))
            except Exception as exc:  # noqa: BLE001 - optional artwork: a seller refusing bots never ends the run
                self.log.info("no provider artwork for %s: %s", event.source_event_id, exc)
        if image:
            event.images, event.image_url = [image], image

    async def _paginate(self, url: str, key: str, params: list[tuple[str, Any]]) -> list[dict[str, Any]]:
        size = self.settings.page_size

        async def page(index: int) -> tuple[list[dict[str, Any]], int | None]:
            payload = await self.get_json(url, params=[("limit", size), ("offset", index * size), *params], expect=dict)
            return payload.get(key) or [], payload.get("count")

        return await self.paginate(page, size, self.settings.max_pages)


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
        min_price=to_decimal(item.get("minPrice")),
        max_price=to_decimal(item.get("maxPrice")),
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
