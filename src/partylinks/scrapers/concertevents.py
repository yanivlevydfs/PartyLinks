"""Concert (concert-events.com) - Israeli guide to Mizrahi / Mediterranean live shows, club nights and
concerts nationwide. Most shows are sold by phone or WhatsApp at the venue; the site links a ticket
page now and then.

A Next.js site with no public API (robots.txt disallows ``/api/``). Read per run:

* ``/sitemap.xml``: every event page ever listed (~700, ``/events/<hebrew slug>``), **in event-date
  order** (checked Nov 2026: 686 URLs, no inversion). The upcoming catalogue is its tail, so a binary
  search over the event pages finds the first current one (~10 pages, the only past pages read).
* Each event page from there to the end, nearest first:
  - schema.org ``MusicEvent`` JSON-LD: title, local ``startDate`` (date alone when the time is not
    set), place name, street address, city (``addressLocality``), performers, image, description;
  - the genre chips (``div.rounded-full.bg-secondary``): the site's categories (מזרחית, ים תיכוני...);
  - the React Server Component payload (``self.__next_f``): the stable event id (``eventId``) and the
    contact block - phone, ticket link (``reservationUrl``), price.

The site has no coordinates, no end time and rarely a price: events are placed by venue + address.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import unquote, urlsplit

from pydantic import Field
from selectolax.lexbor import LexborHTMLParser

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import ParseError, clean_str, has_time, jsonld_nodes, parse_iso, to_decimal
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_RSC_CHUNK = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', re.DOTALL)
_EVENT_ID = re.compile(r'"eventId":"([^"]+)"')
_CONTACT = re.compile(
    r'"contact":\{"phone":(null|"[^"]*"),"reservationPhone":(?:null|"[^"]*"),"reservationUrl":(null|"[^"]*")'
)
_PRICE = re.compile(r'"event":\{"id":"[^"]*","slug":"[^"]*",.*?"price":"([^"]*)"')


class ConcertEventsSettings(SourceSettings):
    site_url: str = "https://concert-events.com"
    sitemap_path: str = "/sitemap.xml"
    event_path_prefix: str = "/events/"
    genre_chip_selector: str = "div.rounded-full.bg-secondary"
    event_types: list[str] = Field(default_factory=lambda: ["concert"])
    # Contact links that are not ticket pages.
    non_ticket_hosts: list[str] = Field(
        default_factory=lambda: ["wa.me", "whatsapp.com", "instagram.com", "facebook.com", "tiktok.com"]
    )


class ConcertEventsScraper(BaseScraper[ConcertEventsSettings]):
    name = "concertevents"
    settings_model = ConcertEventsSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        urls = sitemap_event_urls(await self.get_text(s.site_url + s.sitemap_path), s.event_path_prefix)
        self.log.debug("sitemap lists %d event pages", len(urls))
        pages: dict[int, ScrapedEvent | None] = {}
        first = await self._first_current(urls, pages)
        self.log.debug("upcoming from sitemap index %d: %d events", first, len(urls) - first)
        todo = [i for i in range(first, len(urls)) if i not in pages]
        for i in range(first, len(urls)):  # the search's own current pages, nearest first
            event = pages.get(i)
            if event is not None and self._current(event):
                yield event
        async for event in self.stream_isolated(todo, lambda i: self._fetch(urls[i]), label=lambda i: urls[i]):
            if self._current(event):
                yield event

    async def _first_current(self, urls: list[str], pages: dict[int, ScrapedEvent | None]) -> int:
        """Lowest sitemap index whose event is current (the sitemap is in date order)."""
        low, high = 0, len(urls)
        while low < high:
            mid = (low + high) // 2
            try:
                pages[mid] = event = await self._fetch(urls[mid])
            except Exception as exc:  # noqa: BLE001 - one bad page: read the rest, from here on
                self.record_failure(f"event {urls[mid]}", exc)
                return low
            if event is not None and self._current(event):
                high = mid
            else:
                low = mid + 1
        return low

    def _current(self, event: ScrapedEvent) -> bool:
        return is_current(event.start_at, event.end_at, RetentionSettings(), self.timezone, time_known=event.time_known)

    async def _fetch(self, url: str) -> ScrapedEvent | None:
        return parse_event(await self.get_text(url), url, self.settings, self.timezone)


def sitemap_event_urls(sitemap: str, prefix: str) -> list[str]:
    """Event page URLs in sitemap order (= event date order)."""
    return list(dict.fromkeys(u for u in _LOC.findall(sitemap) if prefix in u))


def rsc_payload(page_html: str) -> str:
    """The page's React Server Component stream, unescaped."""
    return "".join(json.loads(f'"{chunk}"') for chunk in _RSC_CHUNK.findall(page_html))


def _music_event(page_html: str) -> dict[str, Any] | None:
    for node in jsonld_nodes(page_html):
        kind = node.get("@type")
        kinds = kind if isinstance(kind, list) else [kind]
        if any(k in ("MusicEvent", "Event") for k in kinds):
            return node
    return None


def _ticket_link(value: str | None, settings: ConcertEventsSettings) -> str | None:
    if not value or not value.startswith("http"):
        return None
    host = (urlsplit(value).hostname or "").removeprefix("www.")
    if any(host == h or host.endswith("." + h) for h in settings.non_ticket_hosts):
        return None
    return value


def parse_event(page_html: str, url: str, settings: ConcertEventsSettings, timezone: str) -> ScrapedEvent | None:
    node = _music_event(page_html)
    if node is None:
        raise ParseError(f"{url}: no MusicEvent JSON-LD")
    title = clean_str(node.get("name"))
    start_raw = clean_str(node.get("startDate"))
    start_at = parse_iso(start_raw, timezone)
    if not title or start_at is None:
        raise ParseError(f"{url}: no name or start date")
    end_at = parse_iso(node.get("endDate"), timezone)

    place = node.get("location") if isinstance(node.get("location"), dict) else {}
    address = place.get("address") if isinstance(place.get("address"), dict) else {}
    venue = clean_str(place.get("name"))
    street = clean_str(address.get("streetAddress"))
    city = clean_str(address.get("addressLocality"))
    if not city and street:  # "אחי דקר 6, ראשון לציון" or the city alone ("תל אביב")
        city = clean_str(street.rsplit(",", 1)[-1])
    if street and city and street == city:
        street = None  # the city alone is no street address

    performers = node.get("performer") or []
    if isinstance(performers, dict):
        performers = [performers]
    artists = [n for p in performers if isinstance(p, dict) and (n := clean_str(p.get("name")))]

    image = node.get("image")
    if isinstance(image, list):
        image = image[0] if image else None
    if isinstance(image, dict):
        image = image.get("url")
    images = [image] if isinstance(image, str) and image.startswith("http") else []

    chips = LexborHTMLParser(page_html).css(settings.genre_chip_selector)
    genres = list(dict.fromkeys(g.lower() for c in chips if (g := clean_str(c.text(strip=True)))))

    rsc = rsc_payload(page_html)
    event_id = _EVENT_ID.search(rsc)
    contact = _CONTACT.search(rsc)
    phone = json.loads(contact.group(1)) if contact else None
    ticket = _ticket_link(json.loads(contact.group(2)) if contact else None, settings)
    price_match = _PRICE.search(rsc)
    price = to_decimal(price_match.group(1)) if price_match else None

    slug = unquote(url.rstrip("/").rsplit("/", 1)[-1])
    extra: dict[str, Any] = {}
    if ticket:
        extra["purchase_url"] = ticket
    if phone:
        extra["phone"] = phone
    if str(node.get("eventStatus") or "").endswith(("EventCancelled", "EventPostponed")):
        return None
    return ScrapedEvent(
        source=ConcertEventsScraper.name,
        source_event_id=event_id.group(1) if event_id else slug,
        url=url,
        title=title,
        description=clean_str(node.get("description")),
        start_at=start_at,
        end_at=end_at if end_at and end_at > start_at else None,
        time_known=has_time(start_raw),
        venue_name=venue,
        address=street,
        city=city,
        source_genres=genres,
        event_types=list(settings.event_types),
        artists=artists,
        images=images,
        min_price=price if price else None,
        max_price=price if price else None,
        extra=extra,
    )
