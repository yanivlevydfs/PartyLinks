"""Xceed (xceed.me) - a Barcelona-born ticketing app for clubs, club nights, concerts and festivals,
read whole: every city and country it sells in, every kind of event, with its artists and venues.

robots.txt disallows only /reset and /authenticate. The site's own front end calls a public JSON API
(no key, Cloudflare in front, no rate limit seen at 10 requests a second; Nov 2026), read the same way:

- ``event-b2c.xceed.me/v1/events?startTime=<now>&limit=200&offset=..`` - the whole upcoming catalogue,
  every city at once (no city filter), 200 per page (the API's maximum): ~2,400 events in ~170 cities of
  ~23 countries, 12-13 pages. Each item has the start / end (unix), artwork, the venue with its address,
  coordinates, city, time zone, currency and ISO country, the music genres and the lineup with each
  artist's photo, Spotify / SoundCloud / Mixcloud / website links. Sorted soonest first here.
- ``offer-b2c.xceed.me/v1/offers/sales?eventIds[]=..`` - sale status and the lowest price with its
  currency, 25 events a request (the API's maximum): ~95 requests.
- ``event-b2c.xceed.me/v1/events/<id>`` - one per event (~3 KB): the description, the vibes (the event
  types: concert, live show, theatre & comedy, boat party...), festival flag, age policy, organiser and
  the private flag (private events are dropped).
- ``event-b2c.xceed.me/v1/venues/<slug>`` - a venue's "about" and links for its profile (venue_profiles),
  plus its picture. The ~490 venues with upcoming events are spread over ``venue_cycle`` slots of
  ``venue_slot_hours`` (14 x 12 h: each venue once a week, ~35 venues and ~70 requests a run); a profile
  saved once stays until its slot comes round again.

A run is ~13 + ~95 + ~2,400 + ~70 = ~2,600 small JSON requests and covers the whole catalogue; nothing
rotates but the venue profiles. Artists come from the lineups (no request of their own); the API's
artist bios have no field in ArtistProfile, so they are not kept. Events whose end has passed are not
listed by the API (``startTime``) and are dropped again before yielding (``is_current``).
"""

from __future__ import annotations

import time
import zlib
from collections.abc import AsyncIterator, Iterable
from decimal import Decimal
from typing import Any

from pydantic import Field

from partylinks.artists import ArtistProfile
from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import ParseError, clean_str, from_unix, to_decimal, to_float, to_int, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.venue_profiles import VENUE_PROFILE, VenueProfile


class XceedSettings(SourceSettings):
    site_url: str = "https://xceed.me"
    events_api: str = "https://event-b2c.xceed.me"
    offers_api: str = "https://offer-b2c.xceed.me/v1"
    lang: str = "en"
    page_size: int = 200  # the API refuses more ("Limit must be less than or equal to 200")
    max_pages: int = 60
    sales_batch: int = 25  # the API refuses more ("Parameter eventIds can not be gt 25")
    # Venue profiles: each venue's slot is crc32(venue id) % venue_cycle; this run's slot is the current
    # venue_slot_hours window, so every venue is profiled once per venue_cycle * venue_slot_hours.
    venue_cycle: int = 14
    venue_slot_hours: int = 12
    # Xceed's music genres whose name says something else to our keywords (slug -> our words): "Dance" alone
    # is stage dance (ballet) to us, "Industrial" / "Tekno" / "Bounce" are its techno family.
    genre_words: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "dance": ["edm"],
            "indie-dance": ["אינדי דאנס"],  # "indie dance" would also say stage dance
            "indietronic": ["indie electronic"],
            "experimental": ["experimental electronic"],
            "new-age": ["chillout"],
            "progressive": ["progressive house"],
            "songwriting": ["live show"],
            "bounce": ["techno"],
            "industrial": ["industrial techno"],
            "tekno": ["techno"],
        }
    )
    # Vibes (Xceed's event types) in our words; one missing here is passed by its own name.
    vibe_words: dict[str, str] = Field(default_factory=lambda: {"afterwork": "happy hour"})
    # Vibes that describe the place, not the event: a club night "with food & drinks" is not a food event.
    ignored_vibes: list[str] = Field(default_factory=lambda: ["food-drinks"])
    # Vibes that make an event something other than a party: no default genre for those.
    show_vibes: list[str] = Field(
        default_factory=lambda: ["concert", "live-music", "live-show", "theatre-comedy", "literature-poetry", "sport"]
    )
    # A night with no music genre and no show vibe is still a club night (Xceed sells club venues).
    default_genre: str = "nightlife"
    # Xceed's stand-in pictures ("event-place-holder.jpg", "artist-place-holder.jpg").
    placeholder: str = "/static/entities/"
    # Xceed's own test city: its "events" (Secret Sessions, Pulse Club Techno Event (1)..) are QA fixtures
    # the public API lists with the real ones (8 on 2026-11-23), not public events.
    test_cities: list[str] = Field(default_factory=lambda: ["01test-city"])


class XceedScraper(BaseScraper[XceedSettings]):
    name = "xceed"
    settings_model = XceedSettings
    abroad_only = True

    def default_headers(self) -> dict[str, str]:
        return {"Accept-Language": self.settings.lang}

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        now = int(time.time())
        items = soonest_first(await self._listing(now))
        if not items:
            raise ParseError("the events API lists no upcoming event")
        self.log.debug("listed %d events", len(items))
        sales = await self._sales(items)
        retention = RetentionSettings()
        self.venue_profiles = await self._venues(items, now)

        async def fetch(item: dict[str, Any]) -> ScrapedEvent | None:
            detail = await self.optional(
                f"event {item['legacyId']} detail",
                self.get_json(f"{s.events_api}/v1/events/{item['legacyId']}", params={"lang": s.lang}, expect=dict),
            )
            # A refused or odd detail costs only the description and vibes, never the event.
            data = detail.get("data") if detail and detail.get("success") is True else None
            return parse_event(
                item, data if isinstance(data, dict) else {}, sales.get(item.get("id") or ""), s, self.timezone
            )

        async for event in self.stream_isolated(items, fetch, label=lambda i: str(i.get("legacyId"))):
            if event and is_current(event.start_at, event.end_at, retention, self.timezone):
                yield event

    async def _listing(self, now: int) -> list[dict[str, Any]]:
        s = self.settings

        async def page(index: int) -> tuple[list[dict[str, Any]], None]:
            params = {"startTime": now, "limit": s.page_size, "offset": index * s.page_size, "lang": s.lang}
            rows = unwrap(await self.get_json(f"{s.events_api}/v1/events", params=params))
            if not isinstance(rows, list):
                raise ParseError(f"events page {index}: not a list")
            return rows, None  # unfiltered: a short page is what ends the listing

        found = listed_events(await self.paginate(page, s.page_size, s.max_pages), s.test_cities)
        return list({i["legacyId"]: i for i in found}.values())  # a page shifted by a new event repeats one

    async def _sales(self, items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        s = self.settings
        ids = [i["id"] for i in items if clean_str(i.get("id"))]
        batches = [ids[k : k + s.sales_batch] for k in range(0, len(ids), s.sales_batch)]

        async def fetch(batch: list[str]) -> list[dict[str, Any]]:
            payload = await self.get_json(f"{s.offers_api}/offers/sales", params=[("eventIds[]", i) for i in batch])
            rows = unwrap(payload)
            return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []

        out: dict[str, dict[str, Any]] = {}
        async for rows in self.stream_isolated(batches, fetch, label=lambda b: f"offers of {b[0]}.."):
            out |= {r["eventId"]: r for r in rows if isinstance(r.get("eventId"), str)}
        return out

    async def _venues(self, items: list[dict[str, Any]], now: int) -> list[VenueProfile]:
        """This run's slot of the venues with upcoming events: their profile and picture."""
        s = self.settings
        due = venues_due(items, now, s.venue_cycle, s.venue_slot_hours)

        async def fetch(venue: dict[str, Any]) -> VenueProfile | None:
            page = unwrap(await self.get_json(f"{s.events_api}/v1/venues/{venue['slug']}", expect=dict))
            profile = parse_venue(venue | (page if isinstance(page, dict) else {}), s)
            if profile and profile.logo_url:
                response = await self.optional(f"venue picture {venue['slug']}", self.request("GET", profile.logo_url))
                profile.logo = response.content if response is not None else None
            return profile

        return [p async for p in self.stream_isolated(due, fetch, label=lambda v: f"venue {v.get('slug')}")]


# ----------------------------------------------------------------- listing
def unwrap(payload: Any) -> Any:
    """The API's ``{"success": true, "data": ...}`` envelope; a refusal is a ParseError."""
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise ParseError(f"API refused: {str(payload)[:200]}")
    return payload.get("data")


def listed_events(data: Any, test_cities: Iterable[str] = ()) -> list[dict[str, Any]]:
    """The listing's items that can be read: an id, a slug, a start and a city (not a test city)."""
    rows = data if isinstance(data, list) else []
    skip = set(test_cities)
    return [
        i
        for i in rows
        if isinstance(i, dict) and isinstance(i.get("legacyId"), int) and clean_str(i.get("slug"))
        and isinstance(i.get("startingTime"), int | float) and isinstance(_city(i).get("slug"), str)
        and _city(i)["slug"] not in skip
    ]  # fmt: skip


def soonest_first(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Listing items by start time, ascending (the API's own order is not by date)."""
    return sorted(items, key=lambda i: (i.get("startingTime") or 0, i.get("legacyId") or 0))


def venues_due(items: Iterable[dict[str, Any]], now: float, cycle: int, slot_hours: int) -> list[dict[str, Any]]:
    """The listed venues whose slot is this run's (stable per venue id)."""
    slot = int(now // (slot_hours * 3600)) % max(cycle, 1)
    venues: dict[str, dict[str, Any]] = {}
    for item in items:
        venue = item.get("venue") if isinstance(item.get("venue"), dict) else {}
        if not item.get("venueIsSecret") and isinstance(venue.get("id"), str) and clean_str(venue.get("slug")):
            venues.setdefault(venue["id"], venue)
    return [v for key, v in sorted(venues.items()) if zlib.crc32(key.encode()) % max(cycle, 1) == slot]


def _city(item: dict[str, Any]) -> dict[str, Any]:
    venue = item.get("venue") if isinstance(item.get("venue"), dict) else {}
    return venue.get("city") if isinstance(venue.get("city"), dict) else {}


def _real(url: Any, placeholder: str) -> str | None:
    text = clean_str(url)
    return text if text and text.startswith("http") and placeholder not in text else None


# ----------------------------------------------------------------- events
def event_url(item: dict[str, Any], site_url: str, lang: str) -> str:
    return f"{site_url}/{lang}/{_city(item)['slug']}/event/{item['slug']}/{item['legacyId']}"


def genres_of(item: dict[str, Any], detail: dict[str, Any], s: XceedSettings) -> tuple[list[str], list[str]]:
    """(source_genres, event_types): the music genres in our words, and the vibes / festival flag."""
    genres: list[str] = []
    for g in [*(item.get("musicGenres") or []), *(detail.get("musicGenres") or [])]:
        if isinstance(g, dict) and (name := clean_str(g.get("name"))):
            genres += s.genre_words.get(str(g.get("slug") or ""), [name.lower()])
    vibes = [v for v in detail.get("vibes") or [] if isinstance(v, dict) and clean_str(v.get("name"))]
    types = [
        s.vibe_words.get(str(v.get("slug")), str(v["name"]).strip().lower())
        for v in vibes if v.get("slug") not in s.ignored_vibes
    ]  # fmt: skip
    if detail.get("isFestival") is True:
        types.append("festival")
    if not genres and not any(v.get("slug") in s.show_vibes for v in vibes) and "festival" not in types:
        genres = [s.default_genre]
    return list(dict.fromkeys(genres)), list(dict.fromkeys(types))


def artist_profiles(lineup: Any, placeholder: str) -> list[ArtistProfile]:
    """The real artists of a lineup ("TBA", "Resident Djs" are Xceed's generic entries)."""
    out = []
    for a in lineup if isinstance(lineup, list) else []:
        if not isinstance(a, dict) or a.get("isGeneric") or not (name := clean_str(a.get("name"))):
            continue
        genres = [n for g in a.get("musicGenres") or [] if isinstance(g, dict) and (n := clean_str(g.get("name")))]
        slug = clean_str(a.get("slug"))
        out.append(
            ArtistProfile(
                name=name,
                source=XceedScraper.name,
                kind="dj",
                genre=genres[0].lower() if genres else None,
                image_url=_real(a.get("coverUrl"), placeholder),
                spotify_url=_real(a.get("spotifyProfileUrl"), placeholder),
                profile_url=f"https://xceed.me/en/artist/{slug}" if slug else None,
                links=[
                    u
                    for k in ("soundcloudProfileUrl", "mixcloudProfileUrl", "websiteUrl")
                    if (u := _real(a.get(k), placeholder))
                ],
            )
        )
    return out


def parse_event(
    item: dict[str, Any], detail: dict[str, Any], sale: dict[str, Any] | None, s: XceedSettings, timezone: str
) -> ScrapedEvent | None:
    """A listing item with its detail (may be empty when that request failed) and its sale row; None when
    the event is private."""
    if (detail.get("settings") or {}).get("isPrivate") is True:
        return None
    venue = item.get("venue") if isinstance(item.get("venue"), dict) else {}
    city = _city(item)
    country = (clean_str((city.get("country") or {}).get("isoCode")) or "ZZ").upper()
    tz = clean_str(city.get("timezone")) or timezone
    start, end = from_unix(item.get("startingTime")), from_unix(item.get("endingTime"))
    title = clean_str(item.get("name"))
    if not title or not start:
        raise ParseError(f"event {item.get('legacyId')}: no name or start")
    start = start.astimezone(zone(tz))
    hide_end = bool(
        (item.get("settings") or {}).get("hideEndingTime") or (detail.get("settings") or {}).get("hideEndingTime")
    )
    end = None if hide_end or not end or end <= start else end.astimezone(zone(tz))
    secret = bool(item.get("venueIsSecret"))
    coords = venue.get("coordinates") if isinstance(venue.get("coordinates"), dict) else {}
    lat, lon = to_float(coords.get("latitude")), to_float(coords.get("longitude"))
    if secret or lat is None or lon is None or (lat == 0 and lon == 0):
        lat = lon = None
    genres, types = genres_of(item, detail, s)
    lineup = artist_profiles(item.get("lineup"), s.placeholder)
    price = (sale or {}).get("lowestPriceOffer") or {}
    amount: Decimal | None = to_decimal((price.get("price") or {}).get("amount"))
    currency = clean_str((price.get("price") or {}).get("currency")) or clean_str(city.get("currency")) or "EUR"
    company = detail.get("company") if isinstance(detail.get("company"), dict) else {}
    external = clean_str(item.get("externalSalesUrl") or detail.get("externalSalesUrl"))
    extra: dict[str, Any] = {}
    if external and external.startswith("http"):
        extra["purchase_url"] = external
    if not secret and isinstance(venue.get("id"), str):
        extra[VENUE_PROFILE] = f"{XceedScraper.name}-{venue['id']}"
    images = [
        u for u in [_real(item.get("coverUrl"), s.placeholder) or _real(detail.get("coverUrl"), s.placeholder)] if u
    ]
    return ScrapedEvent(
        source=XceedScraper.name,
        source_event_id=str(item["legacyId"]),
        url=event_url(item, s.site_url, s.lang),
        title=title,
        description=clean_str(detail.get("about")),
        start_at=start,
        end_at=end,
        time_known=True,
        venue_name=None if secret else clean_str(venue.get("name")),
        address=None if secret else clean_str(venue.get("address")),
        city=clean_str(city.get("name")),
        country=None if country == "IL" else country,
        time_zone=None if country == "IL" else tz,
        latitude=lat,
        longitude=lon,
        source_genres=genres,
        event_types=types,
        artists=[a.name for a in lineup],
        artist_profiles=lineup,
        min_age=to_int((detail.get("agePolicy") or {}).get("minimumAge")) or None,
        organizer=clean_str(company.get("commercialName")) or clean_str(company.get("name")),
        images=images,
        currency=currency,
        min_price=amount,
        is_sold_out=(sale or {}).get("status") == "sold_out",
        extra=extra,
    )


# ----------------------------------------------------------------- venues
def venue_page_url(venue: dict[str, Any], s: XceedSettings) -> str | None:
    city = (venue.get("city") or {}).get("slug")
    return f"{s.site_url}/{s.lang}/{city}/venue/{venue['slug']}" if city and venue.get("slug") else None


def parse_venue(venue: dict[str, Any], s: XceedSettings) -> VenueProfile | None:
    """A venue's profile from its API page (the listing's venue merged under it)."""
    name = clean_str(venue.get("name"))
    if not name or not isinstance(venue.get("id"), str):
        return None
    coords = venue.get("coordinates") if isinstance(venue.get("coordinates"), dict) else {}
    links = []
    for entry in venue.get("socialMedia") or []:
        url = entry if isinstance(entry, str) else entry.get("url") if isinstance(entry, dict) else None
        if (u := _real(url, s.placeholder)) and u not in links:
            links.append(u)
    return VenueProfile(
        key=f"{XceedScraper.name}-{venue['id']}",  # = the events' VENUE_PROFILE
        name=name,
        source=XceedScraper.name,
        aliases=[name],
        about=clean_str(venue.get("about")),
        address=clean_str(venue.get("address")),
        latitude=to_float(coords.get("latitude")),
        longitude=to_float(coords.get("longitude")),
        links=links,
        profile_url=venue_page_url(venue, s),
        # The wide picture (the venue page banner), else the logo.
        logo_url=_real(venue.get("coverUrl"), s.placeholder) or _real(venue.get("logoUrl"), s.placeholder),
    )
