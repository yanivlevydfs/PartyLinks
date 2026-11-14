"""OGO abroad (us.ogo.events, de.ogo.events) - OGO's guides to cultural events in the USA and Germany.

The same platform as ``ogo`` (ogo.co.il), one host per country: a sitemap index lists every event page
as ``/<locale>/event/<country>/<city>/<yyyy>/<mm>/<dd>/<slug>-<id>``; only dates within ``days_ahead``
are fetched, in the configured locale, one request each, and each page's schema.org ``Event`` JSON-LD
is parsed by ``ogo.parse_event`` (Russian tags translated there, seller page fills a missing price or
picture). The venue's zone comes from the URL's city (``abroad.local_zone``); a date-only start is
midnight there. robots.txt says ``Disallow: /`` and the pages send ``noindex`` (Nov 2026): read anyway
by the owner's choice.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import cast
from urllib.parse import urljoin

import geonamescache
import httpx

from partylinks.models import ScrapedEvent
from partylinks.parsing import ParseError, zone
from partylinks.places import city_zone
from partylinks.scrapers import ogo
from partylinks.scrapers.abroad import local_zone
from partylinks.scrapers.ogo import OgoScraper, OgoSettings
from partylinks.scrapers.seller_offers import SellerLookup

# https://us.ogo.events/he/event/us/new-york/2026/11/20/<slug>-<id>
_EVENT_URL = re.compile(
    r"^(https?://[^/]+)/[a-z]{2}(/event/([a-z]{2})/([^/]+)/(\d{4})/(\d{2})/(\d{2})/[^/?#]*-[A-Za-z0-9]+)/?$"
)


class OgoAbroadSettings(OgoSettings):
    country: str  # ISO code of the host's country, as in the event paths ("US", "DE")
    default_zone: str  # the zone of a city local_zone does not know
    currency: str  # the country's currency: an event without a priced tier is not in shekels


class _OgoAbroadScraper(OgoScraper):
    settings_model = OgoAbroadSettings
    abroad_only = True

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = cast(OgoAbroadSettings, self.settings)  # settings_model above; OgoScraper types it as OgoSettings
        today = datetime.now(zone(s.default_zone)).date()
        index = await self.get_text(s.site_url + s.sitemap_path)
        sitemaps = [xml for url in ogo._locs(index) if (xml := await self._sitemap(url)) is not None]
        # No lower bound: the URL's date is the first one, and weekly classes and long exhibitions dated
        # months back still run (Nov 2026: all 35 of us.ogo.events' older URLs). The sitemap lists only
        # live events, and one that is over is dropped before it is stored.
        urls = upcoming_event_urls(sitemaps, s.locale, date.min, today + timedelta(days=s.days_ahead))
        if len(urls) > s.max_events:
            self.log.warning("listing truncated at max_events=%d (%d found)", s.max_events, len(urls))
        self._sellers = SellerLookup(self, s)
        async for event in self.stream_isolated(urls[: s.max_events], self._fetch_event, label=lambda u: u):
            yield event

    async def _fetch_event(self, url: str) -> ScrapedEvent | None:
        try:
            page = await self.get_text(url)
        except httpx.HTTPStatusError as exc:
            moved = ogo._NEXT_REDIRECT.search(exc.response.text) if exc.response.is_redirect else None
            if not moved:
                raise
            url = urljoin(url, moved.group(1))
            page = await self.get_text(url)
        s = cast(OgoAbroadSettings, self.settings)
        event = parse_event(page, url, self.name, s.default_zone, s.currency)
        return await self._sellers.fill(event) if event is not None else None


class OgoUsScraper(_OgoAbroadScraper):
    name = "ogo_us"


class OgoDeScraper(_OgoAbroadScraper):
    name = "ogo_de"


def upcoming_event_urls(sitemaps: list[str], locale: str, first: date, last: date) -> list[str]:
    """Event pages dated ``first``..``last`` in ``locale``, each once, soonest first."""
    found: dict[str, date] = {}
    for url in (u for xml in sitemaps for u in ogo._locs(xml)):
        match = _EVENT_URL.match(url)
        if match and first <= (day := date(*map(int, match.group(5, 6, 7)))) <= last:
            found.setdefault(f"{match.group(1)}/{locale}{match.group(2)}", day)
    return sorted(found, key=found.__getitem__)


_STATE = re.compile(r",\s*([A-Z]{2})\b")  # "..., Tenafly, NJ 07670", "..., St. Petersburg, FL, USA"


@lru_cache(maxsize=1)
def _us_zones() -> tuple[dict[tuple[str, str], str], dict[str, str]]:
    """GeoNames' US cities of 15,000+: (name, state) -> zone, and each state's most common zone."""
    by_city: dict[tuple[str, str], str] = {}
    by_state: dict[str, Counter[str]] = defaultdict(Counter)
    for c in geonamescache.GeonamesCache().get_cities().values():
        if c["countrycode"] == "US":
            by_city[(_city_key(c["name"]), c["admin1code"])] = c["timezone"]
            by_state[c["admin1code"]][c["timezone"]] += 1
    return by_city, {state: zones.most_common(1)[0][0] for state, zones in by_state.items()}


@lru_cache(maxsize=1)
def _state_names() -> dict[str, str]:
    return {s["name"].casefold(): code for code, s in geonamescache.GeonamesCache().get_us_states().items()}


def _city_key(name: str) -> str:
    return re.sub(r"[^a-z ]", "", name.casefold().replace("-", " ")).strip()


def us_zone(city: str, address: str | None, default: str) -> str:
    """Zone of a US venue: its state from the address or the URL's city ("long-beach-ca"), then that
    state's city of that name, else the state's main zone. No state: the largest US city of that name
    ("pasadena" is Pasadena, TX), else ``default``, not the country's main zone (Chicago, Nov 2026: Long
    Beach, St. Petersburg FL and Tenafly NJ all got it)."""
    by_city, by_state = _us_zones()
    words = _city_key(city).split()
    slug_state = words.pop().upper() if len(words) > 1 and words[-1].upper() in by_state else None
    name = " ".join(words)
    named = [_state_names().get(p.strip().casefold()) for p in (address or "").split(",")]  # "..., California, ..."
    found = [s for s in [*_STATE.findall(address or ""), *named] if s in by_state]
    state = found[-1] if found else slug_state
    if state:
        return by_city.get((name, state)) or by_state[state]
    return city_zone(name, "US") or default


def parse_event(page_html: str, url: str, source: str, default_zone: str, currency: str = "USD") -> ScrapedEvent | None:
    """The page's event via ``ogo.parse_event``, in the venue's country and zone; None when cancelled."""
    match = _EVENT_URL.match(url)
    if match is None:
        raise ParseError(f"{url}: not an OGO event page")
    country, city = match.group(3).upper(), match.group(4).replace("-", " ")
    tz = local_zone(country, default_zone, city)
    # ogo.parse_event reads the city and id from an Israeli path: same layout, country segment swapped.
    il_url = url.replace(f"/event/{match.group(3)}/", "/event/il/", 1)
    event = ogo.parse_event(page_html, il_url, tz)
    if event is None:
        return None
    if country == "US" and (us_tz := us_zone(city, event.address or event.venue_name, default_zone)) != tz:
        tz = us_tz  # a date-only start is midnight in the venue's zone: parsed again in it
        event = cast(ScrapedEvent, ogo.parse_event(page_html, il_url, tz))
    # Re-validated, not model_copy: the URL must become an HttpUrl again.
    return ScrapedEvent.model_validate(
        {
            **event.model_dump(),
            "source": source,
            "url": url,
            "country": country,
            "time_zone": tz,
            "currency": event.currency if event.tickets else currency,
        }
    )
