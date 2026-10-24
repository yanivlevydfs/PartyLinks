"""OGO (ogo.co.il, formerly makore.app) - a human-moderated guide to cultural events in all of Israel.

Not a box office: each event links to the seller (municipal halls, Smarticket tenants, Kupat Bravo,
Tickchak, theatres...), kept as ``extra["purchase_url"]`` so dedup ties it to that seller's own
listing; most of those sellers are scraped by no other source. Not related to makore.co.il.

robots.txt disallows every URL with a query string and ``/*/filtered`` (the filter pages), so the
catalogue comes from the sitemap: an index of five sitemaps listing every event page (~13k, past
ones included) as ``/ru/event/il/<city>/<yyyy>/<mm>/<dd>/<slug>-<id>``. There is no bulk feed
(RSS: last 50), so it is one request per event: only dates within ``days_ahead`` are fetched (the date is in the path), in the configured locale (``he``: Hebrew name
and description). Each page's schema.org ``Event`` JSON-LD is parsed with the generic parser shared
with Chillz. That JSON-LD is in Russian on every locale for the city, language and tags, so the city
comes from the URL's English slug and tags / language are translated below.
"""

from __future__ import annotations

import html
import re
from collections.abc import AsyncIterator
from datetime import date, datetime, time, timedelta
from urllib.parse import urljoin

import httpx
from pydantic import Field

from partylinks.models import ScrapedEvent
from partylinks.parsing import ParseError, clean_str, jsonld_nodes, schema_types, zone
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.chillz import EVENT_TYPES, parse_page

_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_NEXT_REDIRECT = re.compile(r'NEXT_REDIRECT;\w+;(/[^;"\\]+);30[178]')
# https://www.ogo.co.il/ru/event/il/tel-aviv/2026/11/16/yonat-barak-...-xnGgs5FWzgbbqgweE227fm
_EVENT_URL = re.compile(
    r"^(https?://[^/]+)/[a-z]{2}(/event/il/([^/]+)/(\d{4})/(\d{2})/(\d{2})/[^/?#]*-([A-Za-z0-9]+))/?$"
)
_NO_CITY = "any-city"
_ABROAD = "за границей"
# The site's tags (Russian on every locale) as the category words enrichment knows.
_TAGS = {
    "концерт": "concert",
    "вечеринка": "party",
    "стендап": "stand up",
    "спектакль": "theatre",
    "представление": "show",
    "детское": "kids",
    "лекция": "lecture",
    "встреча": "meeting",  # not "meetup": that word means hi-tech meetups here
    "мастер-класс": "workshop",
    "выставка": "exhibition",
    "открытие": "opening",
    "кинопоказ": "screening",
    "экскурсия": "tour",
    "еда": "food",
    "спорт": "sport",
    "конференция": "conference",
}
_LANGUAGES = {
    "иврит": "he",
    "русский": "ru",
    "английский": "en",
    "французский": "fr",
    "испанский": "es",
    "арабский": "ar",
}


class OgoSettings(SourceSettings):
    site_url: str
    sitemap_path: str  # the sitemap index
    locale: str = "he"  # he | ru | en: language of the fetched pages (name, description)
    # Event pages dated from today to this many days ahead are fetched: ~250 a day near term, one
    # request each, and the site answers 429 above ~2.5 requests/s, so 10 days fit a 30-minute run.
    days_ahead: int = Field(10, ge=1)
    max_events: int = Field(5000, ge=1)


class OgoScraper(BaseScraper[OgoSettings]):
    name = "ogo"
    settings_model = OgoSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        today = datetime.now(zone(self.timezone)).date()
        index = await self.get_text(s.site_url + s.sitemap_path)
        sitemaps = [await self.get_text(url) for url in _locs(index)]
        urls = upcoming_event_urls(sitemaps, s.locale, today, today + timedelta(days=s.days_ahead))
        if len(urls) > s.max_events:
            self.log.warning("listing truncated at max_events=%d (%d found)", s.max_events, len(urls))
        self.log.info("listed %d upcoming events", len(urls))
        async for event in self.stream_isolated(urls[: s.max_events], self._fetch_event, label=lambda u: u):
            yield event

    async def _fetch_event(self, url: str) -> ScrapedEvent | None:
        try:
            page = await self.get_text(url)
        except httpx.HTTPStatusError as exc:
            # A re-listed event's old URL answers 308 with no Location header: Next.js puts the new
            # path in the body instead ("NEXT_REDIRECT;replace;/he/event/...;308").
            moved = _NEXT_REDIRECT.search(exc.response.text) if exc.response.is_redirect else None
            if not moved:
                raise
            url = urljoin(url, moved.group(1))
            page = await self.get_text(url)
        return parse_event(page, url, self.timezone)


def _locs(xml: str) -> list[str]:
    return [html.unescape(u) for u in _LOC.findall(xml)]


def upcoming_event_urls(sitemaps: list[str], locale: str, first: date, last: date) -> list[str]:
    """Event pages dated ``first``..``last`` in ``locale``, each once, soonest first (a cut keeps the nearest)."""
    found: dict[str, date] = {}
    for url in (u for xml in sitemaps for u in _locs(xml)):
        match = _EVENT_URL.match(url)
        if match and first <= (day := date(*map(int, match.group(4, 5, 6)))) <= last:
            found.setdefault(f"{match.group(1)}/{locale}{match.group(2)}", day)
    return sorted(found, key=found.__getitem__)


def parse_event(page_html: str, url: str, default_tz: str) -> ScrapedEvent | None:
    """The page's event; None when cancelled or abroad (the site lists Israeli teams' away games)."""
    match = _EVENT_URL.match(url)
    event = parse_page(page_html, url, default_tz)
    if match is None or event is None:
        raise ParseError(f"{url}: no schema.org event on the page")
    node = next(n for n in jsonld_nodes(page_html) if schema_types(n) & EVENT_TYPES and n.get("name"))
    tags = [t for t in (clean_str(k) for k in str(node.get("keywords") or "").split(",")) if t]
    if _ABROAD in tags or str(node.get("eventStatus") or "").endswith("EventCancelled"):
        return None
    offer = next((o for o in [node.get("offers")] if isinstance(o, dict)), {})
    address = (node.get("location") or {}).get("address") or {}
    tz = zone(default_tz)
    # A date-only event has no end; an end at 23:59:59 local is the site's "until the end of the day".
    end = event.end_at
    if len(str(node.get("startDate") or "")) == 10 or (end and end.astimezone(tz).time() >= time(23, 59)):
        end = None
    # validFrom is the event's own start, not a sale start; tiers without a price say nothing.
    tickets = [t.model_copy(update={"sale_start": None}) for t in event.tickets if t.price is not None]
    language = clean_str(node.get("inLanguage"))
    age_range = clean_str(node.get("typicalAgeRange"))  # "18+", "4+", or a kids' range "2-8"
    min_age = re.fullmatch(r"(\d+)\+", age_range or "")
    data = event.model_dump()
    data.update(
        source=OgoScraper.name,
        source_event_id=match.group(7),
        end_at=end,
        # streetAddress already ends with the city, in English ("(Nagler House) Ben-Zvi Yitzchak St 14, Haifa").
        address=clean_str(address.get("streetAddress")),
        city=None if match.group(3) == _NO_CITY else match.group(3).replace("-", " "),
        # Performer links lead to OGO's own listing pages, not to the artist.
        artist_profiles=[],
        # og:image is a generated share card (/opengraph-image), not artwork.
        images=[i for i in event.images if "/opengraph-image" not in i],
        image_url=None,
        source_genres=[_TAGS.get(t, t) for t in tags],
        min_age=int(min_age.group(1)) if min_age else None,
        tickets=tickets,
        is_sold_out=bool(tickets) and all(t.is_sold_out for t in tickets),
        extra={
            **event.extra,
            "purchase_url": clean_str(offer.get("url")),
            "language": _LANGUAGES.get((language or "").lower(), language),
            "tags": [_TAGS.get(t, t) for t in tags],
            "age_range": age_range,
        },
    )
    return ScrapedEvent.model_validate(data)  # re-validated so the artwork merge sees the filtered images
