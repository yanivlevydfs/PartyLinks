"""Mevalim (mevalim.co.il) - show listings for every configured region (theatre, stand-up, concerts...).

Each region page (e.g. ``/central/``, ``/north/``) holds a table of upcoming performances fed by
``admin-ajax.php`` (``action=mevalim_load_more_events``, 20 rows per page, 1-based
``page``). The request needs the ``mevalimAjax.nonce`` printed on the region page.
Each row carries everything we keep: performance id, title, date+time, hall, city,
category, producer, deal + original price (base64 JSON in ``data-item``) and the
purchase link (``#updates`` when sold out).
Artwork: each event's own page (tickets.mevalim.co.il, Next.js) is fetched in parallel
for its main image (the ``/_next/image?url=`` source) and ``og:image``; a page that
fails only leaves that event without artwork. Sold-out rows have no page to fetch.
Only events that will be kept (``wanted``: e.g. in the target city) and have no stored
artwork are fetched (``refetch_artwork`` forces every page). Fetching every listed event
each hour got the source to answer 429 Too Many Requests.
Artists: mevalim.co.il keeps an artist page per performer (``/shows/<name>/`` singers,
``/stand-up/<name>/`` comedians), child pages of one parent each. They are listed through the
WordPress REST API (100 per request) and their photos resolved in one batched media request
per page; the site's placeholder images are dropped.
"""

from __future__ import annotations

import base64
import html
import json
import re
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field
from selectolax.lexbor import LexborHTMLParser, LexborNode

from partylinks.artists import ArtistProfile
from partylinks.models import ScrapedEvent, TicketTier, TierStatus
from partylinks.parsing import ParseError, clean_str, to_decimal
from partylinks.scrapers.base import BaseScraper, SourceSettings

_NONCE = re.compile(r'var mevalimAjax\s*=\s*\{[^}]*"nonce"\s*:\s*"([^"]+)"')
_PRICE = re.compile(r"\d+(?:\.\d+)?")


class MevalimRegion(BaseModel):
    path: str
    table_id: int  # data-table-id on the region's load-more button


class MevalimArtistCatalog(BaseModel):
    parent: int  # WordPress id of the parent page (/shows/, /stand-up/...)
    kind: str  # stored as the artist type: singer / comedian


class MevalimSettings(SourceSettings):
    site_url: str
    regions: list[MevalimRegion]
    ajax_path: str
    ajax_action: str
    page_size: int = Field(20, ge=1)
    max_pages: int = Field(100, ge=1)
    # Fetch each event page for its artwork (one extra request per event)...
    fetch_artwork: bool = True
    # ...on every run, instead of only for events with no stored artwork yet.
    refetch_artwork: bool = False
    artist_catalogs: list[MevalimArtistCatalog] = Field(default_factory=list)
    pages_api_path: str = "/wp-json/wp/v2/pages"
    media_api_path: str = "/wp-json/wp/v2/media"
    # Media ids of the site's logo / star used when an artist page has no photo.
    placeholder_media: list[int] = Field(default_factory=list)


class MevalimScraper(BaseScraper[MevalimSettings]):
    name = "mevalim"
    settings_model = MevalimSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        events: dict[str, ScrapedEvent] = {}
        for region in self.settings.regions:  # regions overlap (a Tel Aviv show is also "central")
            for event in await self.optional(f"region {region.path}", self._region_events(region)) or []:
                events.setdefault(event.source_event_id, event)
        self.log.info("listed %d events", len(events))
        for catalog in self.settings.artist_catalogs:
            self.artist_profiles += await self.optional(f"artists {catalog.kind}", self._list_artists(catalog)) or []
        if self.settings.artist_catalogs:
            self.log.info("listed %d artists", len(self.artist_profiles))
        if not self.settings.fetch_artwork:
            for event in events.values():
                yield event
            return
        async for event in self.stream_isolated(events.values(), self._with_artwork, label=lambda e: e.source_event_id):
            yield event

    async def _region_events(self, region: MevalimRegion) -> list[ScrapedEvent]:
        s = self.settings
        region_url = s.site_url + region.path
        match = _NONCE.search(await self.get_text(region_url))
        if not match:
            raise ParseError("mevalimAjax nonce not found on region page")
        nonce = match.group(1)

        async def page(index: int) -> tuple[list[LexborNode], None]:
            payload = await self.post_json(
                s.site_url + s.ajax_path,
                data={
                    "action": s.ajax_action,
                    "nonce": nonce,
                    "is_passed": 0,
                    "post_id": region.table_id,
                    "page": index + 1,
                    "source_type": "mevalim",
                },
                headers={"X-Requested-With": "XMLHttpRequest"},
                expect=dict,
            )
            if not payload.get("success"):
                raise ParseError(f"load more refused: {payload.get('data')}")
            return parse_rows(str((payload.get("data") or {}).get("content") or "")), None

        events: dict[str, ScrapedEvent] = {}
        for row in await self.paginate(page, s.page_size, s.max_pages):
            try:
                event = parse_row(row, region_url, self.timezone)
            except Exception as exc:  # noqa: BLE001 - one bad row never drops the listing
                self.record_failure(row.attributes.get("data-mvlm-event-id") or "row", exc)
                continue
            events[event.source_event_id] = event  # pages overlap by a row or two
        return list(events.values())

    async def _with_artwork(self, event: ScrapedEvent) -> ScrapedEvent:
        known = self.known_images.get(event.source_event_id)
        if known and not self.settings.refetch_artwork:  # artwork rarely changes: no request
            event.image_url, event.images = known[0], known
            return event
        # Sold-out rows point at their region page on the main site: no event page to fetch.
        if str(event.url).startswith(self.settings.site_url) or not self.wanted(event):
            return event
        page = await self.optional(f"{event.source_event_id} artwork", self.get_text(str(event.url)))
        images = parse_artwork(page) if page else []
        if images:
            event.image_url, event.images = images[0], images
        return event


    async def _list_artists(self, catalog: MevalimArtistCatalog) -> list[ArtistProfile]:
        s = self.settings
        per_page = 100  # the REST API maximum

        async def page(index: int) -> tuple[list[dict[str, Any]], str | None]:
            params = {"parent": catalog.parent, "per_page": per_page, "page": index + 1,
                      "_fields": "id,title,link,featured_media"}  # fmt: skip
            response = await self.request("GET", s.site_url + s.pages_api_path, params=params)
            pages = response.json()
            if not isinstance(pages, list):
                raise ParseError(f"{response.request.url}: expected a list of pages")
            # The total is required: asking past the last page is an error, not an empty page.
            return [p for p in pages if isinstance(p, dict)], response.headers.get("X-WP-Total")

        pages = await self.paginate(page, per_page, s.max_pages)
        media_ids = sorted({m for p in pages if (m := p.get("featured_media")) and m not in s.placeholder_media})
        photos: dict[int, str] = {}
        for start in range(0, len(media_ids), per_page):
            chunk = ",".join(map(str, media_ids[start : start + per_page]))
            params = {"include": chunk, "per_page": per_page, "_fields": "id,source_url"}
            media = await self.optional("artist photos", self.get_json(s.site_url + s.media_api_path, params=params, expect=list))
            photos |= {m["id"]: m["source_url"] for m in media or [] if isinstance(m, dict) and m.get("source_url")}
        return [a for p in pages if (a := parse_artist_page(p, catalog.kind, photos))]


def parse_artist_page(page: dict[str, Any], kind: str, photos: dict[int, str]) -> ArtistProfile | None:
    name = clean_str(html.unescape(str((page.get("title") or {}).get("rendered") or "")))
    if not name or name.startswith(("הופעות", "סטנדאפ")):  # city hubs: "הופעות חיות בחיפה"
        return None
    return ArtistProfile(
        name=name,
        source=MevalimScraper.name,
        kind=kind,
        image_url=photos.get(page.get("featured_media") or 0),
        profile_url=clean_str(page.get("link")),
    )


def parse_artwork(page_html: str) -> list[str]:
    """Event artwork from its page: the main image first, then the social (og:image) one."""
    tree = LexborHTMLParser(page_html)
    found: list[str] = []
    for img in tree.css("img[src]"):
        src = img.attributes.get("src") or ""
        if "/_next/image" in src:  # Next.js optimizer: the original is the `url` query parameter
            original = parse_qs(urlsplit(html.unescape(src)).query).get("url", [""])[0]
            if original.startswith("http"):
                found.append(original)
                break
    og = tree.css_first('meta[property="og:image"]')
    if og and (content := og.attributes.get("content") or "").startswith("http"):
        found.append(content)
    return list(dict.fromkeys(found))


def parse_rows(fragment: str) -> list[LexborNode]:
    # Bare <tr> outside a table is dropped by the HTML parser.
    return LexborHTMLParser(f"<table><tbody>{fragment}</tbody></table>").css("tr[data-mvlm-event-id]")


def parse_row(row: LexborNode, region_url: str, timezone: str) -> ScrapedEvent:
    attrs = row.attributes
    event_id = clean_str(attrs.get("data-mvlm-event-id"))
    if not event_id:
        raise ParseError("event id missing")
    link = row.css_first("a[href]")
    href = (link.attributes.get("href") or "") if link else ""
    sold_out = link is not None and "link-sold" in (link.attributes.get("class") or "")
    item = _item(attrs.get("data-item"))
    price = to_decimal(item.get("discount")) or _price_text(row)
    original = to_decimal(item.get("price"))
    categories = [c for c in (clean_str(x) for x in (attrs.get("data-mvlm-event-category") or "").split(",")) if c]
    # Drop tracking parameters; sold-out rows have no purchase link, so they point at the region page.
    url = urlunsplit(urlsplit(href)._replace(query="", fragment="")) if href.startswith("http") else region_url
    return ScrapedEvent(
        source=MevalimScraper.name,
        source_event_id=event_id,
        url=url,
        title=clean_str(attrs.get("data-mvlm-event-name")) or "",
        start_at=_parse_start(attrs.get("data-mvlm-event-date"), timezone),
        venue_name=_text(row, ".rgbcode_table_shortcode_table_hall"),
        city=_text(row, ".rgbcode_table_shortcode_table_city"),
        event_types=categories,
        organizer=clean_str(attrs.get("data-mvlm-brand")),
        min_price=price,
        max_price=price,
        is_sold_out=sold_out,
        tickets=(
            [TicketTier(name="כרטיס", price=price, is_free=price == 0, status=TierStatus.SOLD_OUT if sold_out else TierStatus.ON_SALE)]
            if price is not None
            else []
        ),
        extra={
            "original_price": float(original) if original is not None and original != price else None,
            "ticket_site": clean_str(attrs.get("data-mvlm-event-domain")),
        },
    )


def _item(raw: str | None) -> dict[str, Any]:
    """Analytics payload: base64 JSON with ``price`` (original) and ``discount`` (deal price)."""
    try:
        data = json.loads(base64.b64decode(raw or ""))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _price_text(row: LexborNode) -> Any:
    match = _PRICE.search(_text(row, ".rgbcode_table_shortcode_table_link_price") or "")
    return to_decimal(match.group(0)) if match else None


def _text(row: LexborNode, selector: str) -> str | None:
    node = row.css_first(selector)
    return clean_str(node.text()) if node else None


def _parse_start(value: Any, timezone: str) -> datetime | None:
    """Local ``dd.mm.yy HH:MM``."""
    text = clean_str(value)
    if not text:
        return None
    return datetime.strptime(text, "%d.%m.%y %H:%M").replace(tzinfo=ZoneInfo(timezone))
