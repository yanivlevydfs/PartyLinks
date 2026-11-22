"""Glatticket (home.glatticket.co.il) - Tickchak's box office for the Haredi and religious public.

The site is a Tickchak LIVE "hub" (Next.js). Its search box calls the hub API on api-live.tickchak.co.il:
``GET /hub/<hubId>/searchData?text=&offset=N`` with an empty text lists the hub's whole archive, the
current events first (44-75 per page; the limit it is sent is ignored), then the past ones
(``isPastEvent``, ~1,150): paging stops at the first page that holds a past one (2 pages, Nov 2026).
An organiser's own hub on the site (``type: "hub"`` items, tiferet.glatticket.co.il) holds a few events
that are not on the main one: each is read the same way, once. Each item gives the title, start and end
(unix), the venue's name only, artwork, description and a category id (the hub's names in settings).
Items without a date (a membership club, a WhatsApp community, a holiday package) are no event.

Each event's own page (glatticket.co.il/<eid>, one request each, ~70) adds what the list lacks: the map
pin and address (its Google Maps link: ``q=<lat>,<lng> (<street>, <no.>, <town>)``), the ticket tiers
(JSON-LD offers, seller_page) and the organiser. A women's / men's show series redirects to a Tickchak
LIVE page with neither: those keep the list's data. robots.txt allows everything.

Overlap: OGO, the Jerusalem municipality, Tickchak LIVE and makore list most of these (48 of 55, Nov
2026), linking to this glatticket.co.il/<eid> page: dedup merges them through it.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import unquote_plus, urlsplit

from pydantic import Field

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, from_unix, jsonld_nodes, schema_types, to_float, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.seller_page import seller_tiers
from partylinks.scrapers.tickchak import soonest_first

# The event page's map link: "maps?q=31.66%2C34.57+%28<street>%2C+12%2C+<town>%29".
_MAP = re.compile(r"google\.[a-z.]+/maps\?q=([^\"'<>\s&]+)")
_PIN = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*(?:\((.*)\))?\s*$", re.DOTALL)
# Auto-made screenshots of the event's page: artwork only when the event has nothing else.
_SCREENSHOT = ("/caps/", "mobileScreenshot_")


class GlatticketSettings(SourceSettings):
    site_url: str = "https://home.glatticket.co.il"  # the hub's home (Origin / Referer of the API calls)
    api_url: str = "https://api-live.tickchak.co.il"
    hub_id: str = "hKpLyNdxl"  # the main hub
    search_path: str = "/hub/{hub_id}/searchData"
    event_url: str = "https://glatticket.co.il/{eid}"  # the page OGO and the municipality link to
    hub_domain: str = "glatticket.co.il"  # sub-hubs read: those whose pages are on this domain
    max_pages: int = Field(10, ge=1)  # per hub; paging stops at the first past event long before
    event_types: list[str] = Field(default_factory=lambda: ["ticket", "area"])  # not hubs, listings, donations
    # The hub's category names by id (its page state, Nov 2026); 2132 / 2133 are the women's / men's
    # show series. Unknown ids get no label: the title and description name the kind.
    categories: dict[int, str] = Field(
        default_factory=lambda: {
            0: "כללי", 1: "הופעות", 2: "סיורים", 3: "מסיבות", 4: "אמנות", 5: "כנסים", 6: "ספורט", 7: "סטנדאפ",
            949: "הצגות", 950: "הצגות ילדים", 951: "קונצרטים", 952: "הרצאות", 1591: "הופעות מחווה",
            1606: "סרטים", 1863: "מרכז מבקרים", 2013: "מופע רגש", 2045: "הופעות לנשים", 2081: "אירועים לנשים",
            2083: "כנסים", 2132: "הופעות לנשים", 2133: "הופעות לגברים", 2180: "הופעות לנשים", 2181: "סרטים",
            2187: "הופעות לגברים", 2188: "הופעות בהפרדה",
        }
    )  # fmt: skip
    # Not events: donations and crowdfunding (17), classes (49), season subscriptions (1945), holidays (1187).
    skip_categories: list[int] = Field(default_factory=lambda: [17, 49, 1187, 1945])
    # Venues the site names without their town and whose page has no map link (Nov 2026).
    venue_cities: dict[str, str] = Field(
        default_factory=lambda: {
            "בנייני האומה": "ירושלים", "בניני האומה": "ירושלים", "מערת צדקיהו": "ירושלים",
            "היכל תרבות רמות": "ירושלים", "היכל התרבות רמות": "ירושלים",
        }
    )  # fmt: skip


class GlatticketScraper(BaseScraper[GlatticketSettings]):
    name = "glatticket"
    settings_model = GlatticketSettings

    def default_headers(self) -> dict[str, str]:
        return {"Origin": self.settings.site_url, "Referer": self.settings.site_url + "/"}

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        events: list[ScrapedEvent] = []
        for item in (await self._listing()).values():
            try:
                event = parse_item(item, self.settings, self.timezone)
            except Exception as exc:  # noqa: BLE001 - one odd item never drops the others
                self.record_failure(f"event {item.get('eid')}", exc)
                continue
            if event is not None and is_current(event.start_at, event.end_at, RetentionSettings(), self.timezone):
                events.append(event)
        # Soonest first: a run a deploy cuts already holds this week.
        pages = self.stream_isolated(soonest_first(events), self._with_page, lambda e: f"event {e.source_event_id}")
        async for event in pages:
            yield event

    async def _listing(self) -> dict[int, dict[str, Any]]:
        """Every current item of the main hub and of the sub-hubs it lists, by event id."""
        s = self.settings
        found: dict[int, dict[str, Any]] = {}
        todo, seen = [s.hub_id], set()
        while todo:
            hub = todo.pop(0)
            if hub in seen:
                continue
            seen.add(hub)
            # The main hub must load; a sub-hub failing costs only its own few events.
            listing = self._hub_items(hub)
            items = await listing if hub == s.hub_id else await self.optional(f"hub {hub}", listing)
            for item in items or []:
                if item.get("type") == "hub":
                    if (sub := sub_hub(item, s.hub_domain)) and sub not in seen:
                        todo.append(sub)
                elif isinstance(item.get("eid"), int):
                    found.setdefault(item["eid"], item)
        self.log.debug("%d hubs, %d current items", len(seen), len(found))
        return found

    async def _hub_items(self, hub: str) -> list[dict[str, Any]]:
        """The hub's current items: its search list, current first, read until its first past item."""
        s = self.settings
        url = s.api_url + s.search_path.format(hub_id=hub)
        items: list[dict[str, Any]] = []
        for _ in range(s.max_pages):
            page = await self.get_json(url, params={"text": "", "offset": len(items)}, expect=list)
            rows = [row for row in page if isinstance(row, dict)]
            current = [row for row in rows if not row.get("isPastEvent")]
            items += current
            if not rows or len(current) < len(rows):
                return items
        self.log.warning("hub %s: no past item within max_pages=%d", hub, s.max_pages)
        return items

    async def _with_page(self, event: ScrapedEvent) -> ScrapedEvent:
        """The event with its page's pin, address, tiers and organiser; as listed when the page fails."""
        page = await self.optional(f"page {event.source_event_id}", self.get_text(str(event.url)))
        if page:
            apply_page(event, page, str(event.url))
        return event


def sub_hub(item: dict[str, Any], domain: str) -> str | None:
    """The hub id of a sub-hub item whose page is on ``domain`` (tiferet.glatticket.co.il/<hubId>)."""
    host = (urlsplit(clean_str(item.get("url")) or "").hostname or "").lower()
    if host != domain and not host.endswith("." + domain):
        return None
    return clean_str(item.get("hubId")) or clean_str(item.get("name"))


def _artwork(item: dict[str, Any]) -> list[str]:
    """The item's banner and share images, page screenshots last."""
    values = [(item.get("image") or {}).get("image"), *(item.get(k) for k in ("fbImg", "fbImgMobile", "fbImgPost"))]
    found = [url for v in values if (url := clean_str(v)) and url.startswith(("http://", "https://"))]
    unique = list(dict.fromkeys(found))
    return sorted(unique, key=lambda url: any(mark in url for mark in _SCREENSHOT))  # stable: order kept otherwise


def parse_item(item: dict[str, Any], settings: GlatticketSettings, timezone: str) -> ScrapedEvent | None:
    """One hub item -> event; None for a hub, a non-event (no date, a class, a donation) or no title."""
    eid, title = item.get("eid"), clean_str(item.get("title"))
    if not isinstance(eid, int) or not title or item.get("type") not in settings.event_types:
        return None
    category = item.get("eventCategory")
    if category in settings.skip_categories:
        return None
    start = from_unix(item.get("timeStart") or item.get("date"))
    if start is None:
        return None
    tz = zone(timezone)
    start = start.astimezone(tz)
    end = from_unix(item.get("timeEnd"))
    venue = clean_str(" ".join(str((item.get("venue") or {}).get("title") or item.get("location") or "").split()))
    label = settings.categories.get(category) if isinstance(category, int) else None
    url = settings.event_url.format(eid=eid)
    images = _artwork(item)
    return ScrapedEvent(
        source=GlatticketScraper.name,
        source_event_id=str(eid),
        url=url,
        title=" ".join(title.split()),
        description=clean_str(item.get("description")),
        start_at=start,
        end_at=end.astimezone(tz) if end and end > start else None,
        venue_name=venue,
        city=settings.venue_cities.get(venue) if venue else None,
        source_genres=[label] if label else [],
        images=images,
        image_url=images[0] if images else None,
        is_sold_out=bool(item.get("soldOut")),
        extra={"purchase_url": url, "category_id": category},
    )


def read_pin(page_html: str) -> tuple[float, float, str | None] | None:
    """(lat, lng, address) of the page's map link; None without one or with a broken pin."""
    link = _MAP.search(page_html)
    match = _PIN.match(unquote_plus(link.group(1))) if link else None
    if not match:
        return None
    lat, lng = to_float(match.group(1)), to_float(match.group(2))
    if lat is None or lng is None or not (-90 <= lat <= 90 and -180 <= lng <= 180) or (lat == 0 and lng == 0):
        return None
    return lat, lng, clean_str(" ".join((match.group(3) or "").split()))


def _town(address: str | None) -> str | None:
    """The address's last part: "שדרות שז"ר, 1, ירושלים" -> ירושלים; "נהריה" -> נהריה."""
    last = clean_str((address or "").rsplit(",", 1)[-1])
    return last if last and not last.isdigit() else None


def apply_page(event: ScrapedEvent, page_html: str, url: str) -> None:
    """Fill the event from its page: pin, address and town, tiers, organiser."""
    if pin := read_pin(page_html):
        event.latitude, event.longitude, event.address = pin
        event.city = _town(event.address) or event.city
    if tiers := seller_tiers(page_html, url):
        event.tickets = tiers
    nodes = [n for n in jsonld_nodes(page_html) if "event" in schema_types(n)]
    if len(nodes) == 1 and isinstance(organizer := nodes[0].get("organizer"), dict):
        event.organizer = clean_str(organizer.get("name")) or event.organizer
