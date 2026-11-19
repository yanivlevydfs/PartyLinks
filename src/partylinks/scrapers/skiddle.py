"""Skiddle (skiddle.com) - the UK and Ireland's ticket site: club nights, gigs, festivals, comedy, theatre,
arts, experiences, food, sport and family days.

The whole catalogue is the goal. Two paths:

* **The official API** (``api_key`` set, from ``PARTYLINKS_SOURCES__SKIDDLE__API_KEY``): ``/api/v1/events/
  search/`` pages through every upcoming event of every country and type (``limit`` 100, ``offset``,
  ``minDate`` today, ``description=1`` for genres and artists): one request per 100 events. Keys are free
  but issued for non-commercial use and must be asked for (https://www.skiddle.com/api/join.php); every
  request names the app in its User-Agent (``api_user_agent``), as Skiddle's client guidelines ask. The
  API sits outside the site's WAF challenge (it answered 400 "A valid API Key must be provided" while the
  pages were challenged, Nov 2026). Its rows are assumed to have the pages' row shape below (``EventCode``,
  ``eventname``, ``venue``, ``startdate``...) plus ``genres``: not yet seen live, for want of a key.
* **Without a key, the city pages in rotation.** The general sitemap (on the CDN, outside the WAF) lists
  every city Skiddle covers (``/whats-on/<City>/``, ~220). A city page embeds, in ``__NEXT_DATA__``
  (``landingPageCategoryDisplayData``), the trending events of each of its ten categories (clubs, gigs,
  festivals, comedy, theatre & arts, experiences, food & drink, family, sport, seasonal): ~70 events of
  all types a request, and each category's ``totalCount``. A run reads ``pages_per_run`` cities, the next
  run the next ones (the slot is the clock divided by ``rotation_hours``), so every city is read once a
  cycle. ``nearestVenues`` on the page gives ~12 venues with their "about", picture and opening hours.

The site sits behind AWS WAF, which answers a JS challenge (202, ``x-amzn-waf-action``) once one IP has
asked for a handful of pages (Nov 2026, measured from here: the 8th page at 15 s apart, the 7th at 60 s
apart; it lifted again within 3-8 minutes). The challenge is not worked around: the first one ends the
run's page reading (the pages already read are kept), and a run that read none is ``blocked``; it is not
reported to the base client as a block, which would move the run to another proxy. The site's own paging
(``?offset=``) is disallowed by robots.txt and not used, so a page gives each category's first rows only:
London's ~2,000 listed events are not all reached without the API.

A row gives id, name, link, type (``EventCode``), start / end, doors, the venue (id, name, street,
postcode, town, ISO country, coordinates), min / max price, currency, ticket status, minimum age,
artists (Spotify id and picture) and the artwork. Its start and end carry ``+00:00`` but are the venue's
local clock (the page's own JSON-LD gives ``+01:00`` for the same summer nights), so the clock is read in
the venue's zone. Pages' rows have no genre: the type maps to our genres (``event_codes``), and the
title and description carry the music. The artists sitemap gives each artist's Skiddle page.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Iterable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import Field

from partylinks.artists import ArtistProfile
from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import ParseError, clean_str, next_data, to_decimal, to_float, to_int, zone
from partylinks.retention import is_current
from partylinks.scrapers.abroad import local_zone
from partylinks.scrapers.base import BaseScraper, SourceBlockedError, SourceSettings
from partylinks.venue_profiles import VENUE_PROFILE, VenueProfile

# The money of the countries Skiddle sells in, for a row that names none.
LOCAL_CURRENCY = {"GB": "GBP", "IE": "EUR", "ES": "EUR", "NL": "EUR", "FR": "EUR", "PT": "EUR", "DE": "EUR"}
REFUSALS = frozenset({403, 429, 503})
_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")


class SkiddleSettings(SourceSettings):
    site_url: str = "https://www.skiddle.com"
    # --- the official API (off without a key; the key comes from the environment, never the yaml)
    api_key: str | None = None
    api_url: str = "https://www.skiddle.com/api/v1/events/search/"
    api_user_agent: str = "SPOTX/1.0 (+https://spotx.fun)"
    api_page_size: int = Field(100, ge=1, le=100)
    # --- without a key: every city page, a few a run
    sitemap_url: str = "https://d1plawd8huk6hh.cloudfront.net/sitemaps/general.xml"
    artists_sitemap_url: str = "https://d1plawd8huk6hh.cloudfront.net/sitemaps/artists1.xml"
    city_path: str = "/whats-on/{city}/"
    # Skiddle's own menu of cities, added to the sitemap's (it misses Dublin) and read alone when it fails.
    menu_cities: list[str] = Field(default_factory=lambda: [
        "London", "Manchester", "Birmingham", "Glasgow", "Leeds", "Liverpool", "Bristol", "Edinburgh", "Newcastle",
        "Nottingham", "Sheffield", "Cardiff", "Brighton", "Belfast", "Dublin", "Cambridge", "Oxford", "York",
    ])  # fmt: skip
    # Sitemap cities that are not places: online events have no venue.
    skip_cities: list[str] = Field(default_factory=lambda: ["Online"])
    # City pages a run reads (the WAF challenges after a handful), and how long one slot of the rotation lasts:
    # the cron's interval, so each run reads the next cities.
    pages_per_run: int = Field(5, ge=1)
    rotation_hours: float = Field(2, gt=0)
    # Pause between two city pages (the other requests go to the CDN at the http block's pace).
    page_gap_seconds: float = Field(60, ge=0)
    # Skiddle's event types -> the category words our genres know (enrichment.genres / declared_genres).
    event_codes: dict[str, list[str]] = Field(default_factory=lambda: {
        "CLUB": ["nightlife"], "BARPUB": ["nightlife"], "LIVE": ["live music"], "FEST": ["festival"],
        "COMEDY": ["comedy"], "THEATRE": ["theatre"], "DANCE": ["dance"], "ARTS": ["culture"],
        "BOOKS": ["literature"], "FILM": ["film"], "DRIVEIN": ["cinema"], "EXHIB": ["exhibitions"],
        "EXPER": ["attractions"], "KIDS": ["family"], "FOOD": ["food"], "SPORT": ["sport"],
        "HEALTH": ["wellness"], "SEASONAL": ["holidays"], "DATE": ["dating"], "LGB": ["lgbt"],
    })  # fmt: skip
    # A row of a type not in event_codes.
    default_genre: str = "culture"
    sold_out_texts: list[str] = Field(default_factory=lambda: ["sold out"])
    venue_logos: bool = True


def is_challenge(response: httpx.Response) -> bool:
    """AWS WAF's JS challenge: 202 with an empty body and ``x-amzn-waf-action``."""
    return bool(response.headers.get("x-amzn-waf-action")) or (
        response.status_code == 202 and not response.content.strip()
    )


def rotation(items: list[str], per_run: int, slot: int) -> list[str]:
    """The ``per_run`` items of rotation slot ``slot``: consecutive slots walk the whole list, wrapping."""
    if not items:
        return []
    start = (slot * per_run) % len(items)
    return [items[(start + i) % len(items)] for i in range(min(per_run, len(items)))]


class SkiddleScraper(BaseScraper[SkiddleSettings]):
    name = "skiddle"
    settings_model = SkiddleSettings
    abroad_only = True

    def is_blocked(self, response: httpx.Response) -> bool:
        """Never: the challenge is read in ``scrape`` and ends the page reading there. Reported as blocked to
        the base client it would move the run to another proxy, which is working around the WAF."""
        return False

    def is_gone(self, response: httpx.Response) -> bool:
        """A refusal (403, 429, 503, a firewall's own code) fails the request once, on the same IP: the base
        client would otherwise retry it on the next proxy of the pool, working around the site's limit."""
        return response.status_code in REFUSALS or response.status_code >= 400 and response.reason_phrase == ""

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        artist_links = await self.optional("artists sitemap", self._artist_pages()) or {}
        rows = self._api_rows() if s.api_key else self._page_rows()
        retention = RetentionSettings()
        seen: set[str] = set()
        async for row, page_city in rows:
            try:
                event = parse_event(row, s, self.timezone, page_city, artist_links)
            except Exception as exc:  # noqa: BLE001 - one bad row must not cost the others
                self.record_failure(f"event {row.get('id')}", exc)
                continue
            if event is None or event.source_event_id in seen:
                continue
            seen.add(event.source_event_id)
            if is_current(event.start_at, event.end_at, retention, self.timezone):
                yield event
        if s.venue_logos:
            await self._venue_logos()

    # ------------------------------------------------------------------ the API
    async def _api_rows(self) -> AsyncIterator[tuple[dict[str, Any], str | None]]:
        s = self.settings
        params: dict[str, Any] = {
            "api_key": s.api_key, "limit": s.api_page_size, "description": 1,
            "minDate": datetime.now(zone(self.timezone)).date().isoformat(),
        }  # fmt: skip
        headers = {"User-Agent": s.api_user_agent, "Accept": "application/json"}
        offset, total = 0, None
        while total is None or offset < total:
            data = await self.get_json(s.api_url, params=params | {"offset": offset}, headers=headers, expect=dict)
            if str(data.get("error") or "0") not in ("0", "False", "false"):
                raise ParseError(f"skiddle api: {data.get('errormessage') or data.get('error')}")
            results = [r for r in data.get("results") or [] if isinstance(r, dict)]
            total = to_int(data.get("totalcount")) or 0
            for row in results:
                if isinstance(row.get("venue"), dict) and (profile := venue_profile(row["venue"], row.get("link"))):
                    self._add_venue(profile)
                yield row, None
            if not results:
                break
            offset += len(results)

    # ------------------------------------------------------------------ the city pages
    async def _cities(self) -> list[str]:
        s = self.settings
        xml = await self.optional("city sitemap", self.get_text(s.sitemap_url))
        cities = list(dict.fromkeys([*sitemap_cities(xml or "", s.site_url), *s.menu_cities]))
        return [c for c in cities if c not in s.skip_cities]

    async def _page_rows(self) -> AsyncIterator[tuple[dict[str, Any], str | None]]:
        s = self.settings
        cities = await self._cities()
        slot = int(datetime.now(UTC).timestamp() // (s.rotation_hours * 3600))
        chosen = rotation(cities, s.pages_per_run, slot)
        self.log.info("rotation slot %d: %s of %d cities (%s)", slot, len(chosen), len(cities), ", ".join(chosen))
        read = 0
        for i, city in enumerate(chosen):
            if i and s.page_gap_seconds:
                await asyncio.sleep(s.page_gap_seconds)
            url = s.site_url + s.city_path.format(city=city)
            try:
                response = await self.request("GET", url)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in REFUSALS:
                    self.record_failure(url, exc)  # a gone city page: the other cities still count
                    continue
                if not read:
                    raise SourceBlockedError(f"{self.name}: refused ({exc.response.status_code}) on {url}") from exc
                self.log.warning(
                    "refused (%d) after %d city pages: the run stops at %s", exc.response.status_code, read, city
                )
                return
            if is_challenge(response):
                if not read:
                    raise SourceBlockedError(f"{self.name}: AWS WAF challenge on the first city page ({url})")
                self.log.warning("AWS WAF challenge after %d city pages: the run stops at %s", read, city)
                return
            try:
                rows, venues, counts = city_page(response.text)
            except ParseError as exc:  # a changed page: the other cities still count
                self.record_failure(url, exc)
                continue
            read += 1
            self.log.info("%s: %d rows of %d listed (%s)", city, len(rows), sum(counts.values()), counts)
            for venue in venues:
                if profile := venue_profile(venue, None):
                    self._add_venue(profile)
            for row in rows:
                yield row, city
        if not read:
            raise ParseError("no city page read")

    # ------------------------------------------------------------------ artists and venues
    async def _artist_pages(self) -> dict[str, str]:
        return artist_pages(await self.get_text(self.settings.artists_sitemap_url))

    def _add_venue(self, profile: VenueProfile) -> None:
        if all(p.key != profile.key for p in self.venue_profiles):
            self.venue_profiles.append(profile)

    async def _venue_logos(self) -> None:
        for profile in self.venue_profiles:
            if profile.logo_url and profile.logo is None:
                try:
                    response = await self.request("GET", profile.logo_url)
                except httpx.HTTPError as exc:  # some pictures are gone (403 on the CDN): the profile keeps its text
                    self.log.info("venue picture %s skipped: %s", profile.logo_url, exc)
                    continue
                if response.headers.get("content-type", "").startswith("image/"):
                    profile.logo = response.content


def sitemap_cities(xml: str, site_url: str) -> list[str]:
    """The city slugs of the sitemap's ``/whats-on/<City>/`` pages, in its order, once each."""
    prefix = site_url.rstrip("/") + "/whats-on/"
    out: dict[str, None] = {}
    for loc in _LOC.findall(xml):
        if (
            loc.startswith(prefix)
            and (rest := loc[len(prefix) :]).endswith("/")
            and rest.count("/") == 1
            and rest != "/"
        ):
            out.setdefault(rest[:-1], None)
    return list(out)


def artist_pages(xml: str) -> dict[str, str]:
    """Artist id -> Skiddle page, from the artists sitemap (``/artists/<slug>-<id>/``)."""
    out = {}
    for loc in _LOC.findall(xml):
        if (m := re.search(r"/artists/[^/]*?-(\d+)/$", loc)) is not None:
            out[m.group(1)] = loc
    return out


def city_page(page_html: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """A city page's rows (every category's), its nearest venues and each category's listed total."""
    props = (next_data(page_html).get("props") or {}).get("pageProps") or {}
    categories = props.get("landingPageCategoryDisplayData")
    if not isinstance(categories, list):
        # A category page (/clubs/<City>/) has one list.
        results = props.get("initialResults")
        if not isinstance(results, dict) or not isinstance(results.get("events"), list):
            raise ParseError("city page has no landingPageCategoryDisplayData or initialResults.events")
        categories = [{"id": "results", "data": results["events"], "totalCount": results.get("totalCount")}]
    rows: dict[str, dict[str, Any]] = {}
    counts: dict[str, int] = {}
    for category in categories:
        if not isinstance(category, dict):
            continue
        counts[str(category.get("id"))] = to_int(category.get("totalCount")) or 0
        for row in category.get("data") or []:
            if isinstance(row, dict) and clean_str(row.get("id")) and clean_str(row.get("link")):
                rows.setdefault(str(row["id"]), row)
    venues = [v for v in props.get("nearestVenues") or [] if isinstance(v, dict)]
    return list(rows.values()), venues, counts


def local_clock(value: Any, tz: str) -> datetime | None:
    """``2027-06-12T12:00:00+00:00`` as 12:00 in ``tz``: the offset on a row is not the venue's."""
    text = clean_str(value)
    if not text:
        return None
    try:
        naive = datetime.fromisoformat(text[:19])
    except ValueError:
        return None
    return naive.replace(tzinfo=zone(tz))


def spotify_url(uri: Any) -> str | None:
    """``spotify:artist:<id>`` as its open.spotify.com page."""
    text = clean_str(uri) or ""
    return f"https://open.spotify.com/artist/{text.rsplit(':', 1)[1]}" if text.startswith("spotify:artist:") else None


def venue_page(event_link: Any) -> str | None:
    """The venue's page from one of its event links: ``/whats-on/<City>/<Venue>/<Event>/<id>/`` -> the first three."""
    parts = urlsplit(clean_str(event_link) or "")
    segments = [p for p in parts.path.split("/") if p]
    if len(segments) < 3 or segments[0] != "whats-on":
        return None
    return f"{parts.scheme}://{parts.netloc}/{'/'.join(segments[:3])}/"


def venue_profile(venue: dict[str, Any], event_link: Any) -> VenueProfile | None:
    """A venue the site describes (its "about" or picture); None for a bare name and address."""
    about = clean_str(venue.get("description"))
    picture = clean_str(venue.get("imageUrl")) or clean_str(venue.get("imageurl"))
    name, venue_id = clean_str(venue.get("name")), clean_str(venue.get("id"))
    if not (name and venue_id and (about or picture)):
        return None
    facts = [
        f"{label}: {text}"
        for label, key in (("Type", "type"), ("Opening hours", "openingHours"), ("Music", "musicPolicy"),
                           ("Dress code", "dressCode"), ("Capacity", "capacity"), ("Nearest station", "nearestTrainStation"))
        if (text := clean_str(venue.get(key)))
    ]  # fmt: skip
    street, postcode, town = (
        clean_str(venue.get("address")),
        clean_str(venue.get("postcode")),
        clean_str(venue.get("town")),
    )
    lat, lon = to_float(venue.get("latitude")), to_float(venue.get("longitude"))
    located = lat is not None and lon is not None and (lat, lon) != (0, 0)
    url = clean_str(venue.get("url"))
    return VenueProfile(
        key=f"{SkiddleScraper.name}-{venue_id}",
        name=name,
        source=SkiddleScraper.name,
        aliases=[name],
        about="\n\n".join(p for p in (about, "\n".join(facts)) if p) or None,
        address=", ".join(p for p in (street, town, postcode) if p) or None,
        latitude=lat if located else None,
        longitude=lon if located else None,
        links=[url] if url and url.startswith("http") else [],
        profile_url=venue_page(event_link),
        logo_url=picture if picture and picture.startswith("http") else None,
    )


def row_genres(row: dict[str, Any], s: SkiddleSettings) -> tuple[list[str], list[str]]:
    """``source_genres`` (the type's category words, then the API's genres) and ``event_types`` (the type)."""
    code = (clean_str(row.get("EventCode")) or "").upper()
    words = list(s.event_codes.get(code) or [s.default_genre])
    for genre in row.get("genres") or []:
        name = clean_str(genre.get("name")) if isinstance(genre, dict) else clean_str(genre)
        if name and name.lower() not in words:
            words.append(name.lower())
    return words, [code.lower()] if code else []


def parse_event(
    row: dict[str, Any],
    s: SkiddleSettings,
    timezone: str,
    page_city: str | None = None,
    artist_links: dict[str, str] | None = None,
) -> ScrapedEvent | None:
    """One row (a page's or the API's); None when cancelled. A venue whose town is a street (it has a number)
    is in the city of the page that listed it."""
    if str(row.get("cancelled") or "0") != "0":
        return None
    venue = row.get("venue") if isinstance(row.get("venue"), dict) else {}
    country = (clean_str(venue.get("country")) or "GB").upper()
    town = clean_str(venue.get("town")) or clean_str(venue.get("postcode_lookup"))
    city = town if town and not any(c.isdigit() for c in town) else (page_city or town)
    tz = local_zone(country, "Europe/London", city)
    start, end = local_clock(row.get("startdate"), tz), local_clock(row.get("enddate"), tz)
    title = clean_str(row.get("eventname"))
    if not title or not start:
        raise ParseError(f"skiddle {row.get('id')}: row without name or start")
    lat, lon = to_float(venue.get("latitude")), to_float(venue.get("longitude"))
    located = lat is not None and lon is not None and (lat, lon) != (0, 0)
    street = clean_str(venue.get("address"))
    postcode = clean_str(venue.get("postcode"))
    pricing = row.get("ticketpricing") if isinstance(row.get("ticketpricing"), dict) else {}
    low, high = to_decimal(pricing.get("minPrice")), to_decimal(pricing.get("maxPrice"))
    status = (clean_str(row.get("ticketStatusText")) or "").lower()
    artists = [a for a in row.get("artists") or [] if isinstance(a, dict) and clean_str(a.get("name"))]
    image = next(
        (clean_str(row.get(k)) for k in ("xlargeimageurl", "largeimageurl", "imageurl") if clean_str(row.get(k))), None
    )
    seller = clean_str(row.get("ticketUrl"))
    genres, types = row_genres(row, s)
    extra: dict[str, Any] = {"purchase_url": seller} if seller and seller.startswith("http") else {}
    if venue_id := clean_str(venue.get("id")):
        extra[VENUE_PROFILE] = f"{SkiddleScraper.name}-{venue_id}"
    return ScrapedEvent(
        source=SkiddleScraper.name,
        source_event_id=str(row["id"]),
        url=row["link"],
        title=title,
        description=clean_str(row.get("description")),
        start_at=start,
        end_at=end if end and end > start else None,
        time_known=True,
        venue_name=clean_str(venue.get("name")),
        address=", ".join(p for p in (street, postcode) if p) or None,
        city=city,
        country=country,
        time_zone=tz,
        latitude=lat if located else None,
        longitude=lon if located else None,
        source_genres=genres,
        event_types=types,
        artists=[a["name"].strip() for a in artists],
        artist_profiles=list(_artist_profiles(artists, artist_links or {})),
        min_age=to_int(row.get("minage")) or None,
        images=[image] if image else [],
        currency=clean_str(row.get("eventCurrency"))
        or clean_str(row.get("currency"))
        or LOCAL_CURRENCY.get(country, "GBP"),
        min_price=low,
        max_price=high if high is not None and (low is None or high >= low) else low,
        is_sold_out=any(t in status for t in s.sold_out_texts),
        extra=extra,
    )


def _artist_profiles(artists: Iterable[dict[str, Any]], links: dict[str, str]) -> Iterable[ArtistProfile]:
    for a in artists:
        yield ArtistProfile(
            name=a["name"].strip(),
            source=SkiddleScraper.name,
            image_url=clean_str(a.get("image")),
            spotify_url=spotify_url(a.get("spotifyartisturl")),
            profile_url=links.get(clean_str(a.get("artistid")) or ""),
        )
