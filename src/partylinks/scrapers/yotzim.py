"""Yotzim (yotzim.com) - party guide: Tel Aviv nightlife, nature parties, festivals and Koh Phangan.

robots.txt allows all but ``/api/``, ``/profile``, ``/notifications`` and ``/t/`` (the ticket
redirect, so the seller's link is not followed). Discovery is by category: each category page
(``/genres/techno``, ``/events/nature``, ``/holidays/halloween``...; ``categories`` in config)
links its event cards, and every event takes the genre words of the categories that list it.
The sitemap adds any event no category shows. Category pages also keep past events, so pages of
events already over are remembered (``PAST_FILE``) and never fetched again.
Details: each page's schema.org ``MusicEvent`` JSON-LD gives name, start/end with offset, place
(a venue, or only the city), country, age range, poster, organizer and one ``Offer`` price.
"""

from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urljoin, urlsplit

from partylinks.models import ScrapedEvent, TicketTier, TierStatus
from partylinks.parsing import ParseError, clean_str, has_time, html_to_text, jsonld_nodes, schema_types, to_decimal
from partylinks.scrapers.abroad import country_code, local_zone
from partylinks.scrapers.airdrop import _list, _local
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.chillz import _load_past, _save_past

_EVENT_LINK = re.compile(r'href="(/event/[^"#?]+)"')
_SITEMAP_EVENT = re.compile(r"<loc>(https://[^<]+/event/[^<]+)</loc>")
_AGE = re.compile(r"\d+")
# "Other events that might fit": shown when a category has nothing coming, not of that category.
_SUGGESTED = re.compile(r"<h2[^>]*>אירועים אחרים[^<]*</h2>.*?(?=<h2|</section>|$)", re.DOTALL)
SITE_NAME = "יוצאים!"  # the guide itself, named as organizer of the nights it runs
PAST_FILE = "data/yotzim_past.json"


class YotzimSettings(SourceSettings):
    site_url: str
    sitemap_path: str = "/sitemap.xml"
    # Category path -> genre words its events declare (matched by enrichment's genre keywords).
    categories: dict[str, list[str]]


class YotzimScraper(BaseScraper[YotzimSettings]):
    name = "yotzim"
    settings_model = YotzimSettings

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._past: set[str] = set()
        self._genres: dict[str, list[str]] = {}

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        pages = await asyncio.gather(*(self.get_text(s.site_url + path) for path in s.categories))
        self._genres = category_genres(dict(zip(s.categories, pages, strict=True)), s.categories, s.site_url)
        if not self._genres:
            raise ParseError("no category lists any event")
        sitemap = await self.optional("sitemap", self.get_text(s.site_url + s.sitemap_path))
        urls = list(dict.fromkeys([*self._genres, *_SITEMAP_EVENT.findall(sitemap or "")]))
        self._past = await asyncio.to_thread(_load_past, PAST_FILE)
        todo = [u for u in urls if u not in self._past]
        self.log.info("%d event pages (%d known past skipped)", len(todo), len(urls) - len(todo))
        try:
            async for event in self.stream_isolated(todo, self._fetch_event, label=lambda u: u):
                yield event
        finally:
            _save_past(self._past, PAST_FILE)

    async def _fetch_event(self, url: str) -> ScrapedEvent | None:
        event = parse_event(await self.get_text(url), url, self.timezone, self._genres.get(url, []))
        if event is not None and (event.end_at or event.start_at) < datetime.now(UTC) - timedelta(days=1):
            self._past.add(url)  # over: never fetched again (the service never stores it either)
            return None
        return event


def category_genres(pages: dict[str, str], categories: dict[str, list[str]], site_url: str) -> dict[str, list[str]]:
    """Event page URL -> genre words of every category page listing it (in category order). Events
    only suggested on a page ("other events that might fit") are discovered but not tagged."""
    genres: dict[str, list[str]] = defaultdict(list)
    for path, page_html in pages.items():
        own = set(_EVENT_LINK.findall(_SUGGESTED.sub("", page_html)))
        for href in dict.fromkeys(_EVENT_LINK.findall(page_html)):
            words = genres[urljoin(site_url + "/", href)]
            words += [w for w in categories[path] if w not in words and href in own]
    return dict(genres)


def parse_event(page_html: str, url: str, timezone: str, genres: list[str]) -> ScrapedEvent | None:
    """The page's ``MusicEvent``; None when cancelled."""
    event = next((n for n in jsonld_nodes(page_html) if schema_types(n) & {"event", "musicevent", "festival"}), None)
    if event is None:
        raise ParseError(f"{url}: no Event JSON-LD")
    if str(event.get("eventStatus") or "").endswith("EventCancelled"):
        return None
    location = event.get("location") if isinstance(event.get("location"), dict) else {}
    address = location.get("address") if isinstance(location.get("address"), dict) else {}
    country = country_code(clean_str(address.get("addressCountry")) or "IL")
    zone = local_zone(country, timezone) if country != "IL" else timezone
    title, start = clean_str(event.get("name")), _local(event.get("startDate"), zone, country)
    if not title or not start:
        raise ParseError(f"{url}: Event without name or start")
    city, place = clean_str(address.get("addressLocality")), clean_str(location.get("name"))
    offer = next((o for o in _list(event.get("offers")) if isinstance(o, dict)), {})
    price = to_decimal(offer.get("price"))
    sold_out = str(offer.get("availability") or "").endswith("SoldOut")
    organizer = event.get("organizer") if isinstance(event.get("organizer"), dict) else {}
    age = _AGE.search(str(event.get("typicalAgeRange") or ""))
    images = [i.get("url") if isinstance(i, dict) else i for i in _list(event.get("image"))]
    return ScrapedEvent(
        source=YotzimScraper.name,
        source_event_id=urlsplit(url).path.removeprefix("/event/"),
        url=clean_str(event.get("url")) or url,
        title=title,
        description=html_to_text(str(event.get("description") or "")),
        start_at=start,
        time_known=has_time(event.get("startDate")),
        end_at=_local(event.get("endDate"), zone, country),
        venue_name=place if place != city else None,  # only the city known: no venue
        address=clean_str(address.get("streetAddress")),
        city=city,
        country=None if country == "IL" else country,
        source_genres=genres,
        min_age=int(age.group()) if age else None,
        organizer=None if clean_str(organizer.get("name")) in (None, SITE_NAME) else clean_str(organizer.get("name")),
        images=[i for i in images if isinstance(i, str) and i],
        currency=clean_str(offer.get("priceCurrency")) or "ILS",
        min_price=price,
        max_price=price,
        is_sold_out=sold_out,
        tickets=[TicketTier(name="כרטיס", price=price, status=TierStatus.SOLD_OUT if sold_out else TierStatus.ON_SALE)]
        if price is not None
        else [],
    )
