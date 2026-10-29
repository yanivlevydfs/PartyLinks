"""Yotzim (yotzim.com) - party guide: Tel Aviv nightlife, nature parties, festivals and Koh Phangan.

robots.txt allows all but ``/api/``, ``/profile``, ``/notifications`` and ``/t/`` (the ticket
redirect, so the seller's link is not followed). Discovery is by category: each category page
(``/genres/techno``, ``/events/nature``, ``/holidays/halloween``...; ``categories`` in config)
links its event cards, and every event takes the genre words of the categories that list it.
Each page's "recently" rail (``היה לאחרונה``, events already over) is dropped, so past events are
never fetched and nothing about them is kept (no sitemap: it lists past events too).
Details: each page's schema.org ``MusicEvent`` JSON-LD gives name, start/end with offset, place
(a venue, or only the city), country, age range, poster, organizer and one ``Offer`` price.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urljoin, urlsplit

from partylinks.models import ScrapedEvent, TicketTier, TierStatus
from partylinks.parsing import ParseError, clean_str, has_time, html_to_text, jsonld_nodes, schema_types, to_decimal
from partylinks.scrapers.abroad import country_code, local_zone
from partylinks.scrapers.airdrop import _list, _local
from partylinks.scrapers.base import BaseScraper, SourceSettings, gather_or_cancel

_EVENT_LINK = re.compile(r'href="(/event/[^"#?]+)"')
_AGE = re.compile(r"\d+")
# Rails headed "recently" / "what was at..." ("היה לאחרונה", "מה היה ב...") list events already over;
# "other events that might fit" / "meanwhile, the coming parties" ("אירועים אחרים", "בינתיים") are
# upcoming but not of the page's category. Each runs to the next heading or the end of its section.
_PAST = re.compile(r"<h2[^>]*>\s*(?:היה לאחרונה|מה היה).*?(?=<h2|</section>|$)", re.DOTALL)
_SUGGESTED = re.compile(r"<h2[^>]*>\s*(?:אירועים אחרים|בינתיים).*?(?=<h2|</section>|$)", re.DOTALL)
SITE_NAME = "יוצאים!"  # the guide itself, named as organizer of the nights it runs


class YotzimSettings(SourceSettings):
    site_url: str
    # Category path -> genre words its events declare (matched by enrichment's genre keywords).
    categories: dict[str, list[str]]


class YotzimScraper(BaseScraper[YotzimSettings]):
    name = "yotzim"
    settings_model = YotzimSettings

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._genres: dict[str, list[str]] = {}

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        pages = await gather_or_cancel(self.get_text(s.site_url + path) for path in s.categories)
        self._genres = category_genres(dict(zip(s.categories, pages, strict=True)), s.categories, s.site_url)
        if not self._genres:
            raise ParseError("no category lists any upcoming event")
        self.log.info("%d upcoming event pages", len(self._genres))
        async for event in self.stream_isolated(self._genres, self._fetch_event, label=lambda u: u):
            yield event

    async def _fetch_event(self, url: str) -> ScrapedEvent | None:
        return parse_event(await self.get_text(url), url, self.timezone, self._genres[url])


def category_genres(pages: dict[str, str], categories: dict[str, list[str]], site_url: str) -> dict[str, list[str]]:
    """Upcoming event page URL -> genre words of every category page listing it (in category order).
    Events only suggested on a page ("other events that might fit") are discovered but not tagged;
    the "recently" rail (past events) is skipped."""
    genres: dict[str, list[str]] = defaultdict(list)
    for path, raw in pages.items():
        page_html = _PAST.sub("", raw)
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
