"""Xceed: the public events / offers API (saved answers under tests/fixtures/xceed) to ScrapedEvent,
artist and venue profiles, the category mapping per Xceed type, and the venue rotation."""

from __future__ import annotations

import asyncio
import json
import zlib
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from partylinks.config import HttpSettings, get_settings
from partylinks.enrichment import Enricher
from partylinks.models import ScrapedEvent
from partylinks.parsing import ParseError
from partylinks.scrapers.xceed import (
    XceedScraper,
    XceedSettings,
    artist_profiles,
    event_url,
    genres_of,
    listed_events,
    parse_event,
    parse_venue,
    soonest_first,
    unwrap,
    venues_due,
)
from partylinks.venue_profiles import VENUE_PROFILE

TZ = "Asia/Jerusalem"
FIXTURES = Path(__file__).parent / "fixtures" / "xceed"
S = XceedSettings(cron="1 6,18 * * *")


def _load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


LISTING = _load("listing.json")
DETAILS = _load("details.json")
SALES = {r["eventId"]: r for r in _load("sales.json")["data"]}
ITEMS = {i["legacyId"]: i for i in listed_events(unwrap(LISTING))}


def _event(legacy_id: int, **detail: Any) -> ScrapedEvent:
    item = ITEMS[legacy_id]
    event = parse_event(item, DETAILS[str(legacy_id)] | detail, SALES.get(item["id"]), S, TZ)
    assert event is not None
    return event


def _genres(event: ScrapedEvent) -> list[str]:
    settings = get_settings()
    enriched = Enricher(settings.enrichment, settings.timezone).enrich(event)
    assert enriched is not None
    return enriched.genres


def test_listing_envelope_order_and_event_url() -> None:
    assert sorted(ITEMS) == [242783, 248797, 250782, 251856, 252823]
    assert [i["legacyId"] for i in soonest_first(ITEMS.values())] == [251856, 250782, 242783, 248797, 252823]
    assert event_url(ITEMS[242783], S.site_url, S.lang) == (
        "https://xceed.me/en/barcelona/event/mabemusik-l-secret-room-l-house-tech-house-all-night-long/242783"
    )
    assert listed_events([{"legacyId": 1, "slug": "x"}, "junk", {"name": "no id"}]) == []  # no start / city
    with pytest.raises(ParseError):
        unwrap({"success": False, "error": {"message": "Limit must be less than or equal to 200"}})


def test_events_of_xceeds_test_city_are_dropped() -> None:
    qa = {"legacyId": 213411, "slug": "secret-sessions-5", "startingTime": 1806703140}
    qa["venue"] = {"city": {"slug": "01test-city", "name": "01Test City"}}
    assert listed_events([qa, ITEMS[242783]], S.test_cities) == [ITEMS[242783]]


def test_club_night_in_the_venue_zone_with_its_place_price_lineup_and_profile_key() -> None:
    event = _event(242783)
    madrid = ZoneInfo("Europe/Madrid")
    assert event.start_at == datetime(2026, 11, 18, 23, 59, tzinfo=madrid)
    assert event.end_at == datetime(2026, 11, 19, 5, 0, tzinfo=madrid)
    assert (event.venue_name, event.address, event.city) == ("City Hall", "Rambla de Catalunya, 4", "Barcelona")
    assert (event.country, event.time_zone) == ("ES", "Europe/Madrid")
    assert (event.latitude, event.longitude) == (41.387721, 2.168269)
    assert (event.min_price, event.currency, event.is_sold_out) == (Decimal(15), "EUR", False)
    assert event.source_genres == ["tech house", "house", "techno"]
    assert event.artists == ["MABEMUSIK"]
    assert event.artist_profiles[0].image_url is None  # Xceed's placeholder is no photo
    assert (event.min_age, event.organizer) == (18, "City Hall")
    assert event.description and "SECRET ROOM" in event.description
    assert event.images == [ITEMS[242783]["coverUrl"]]
    assert event.extra[VENUE_PROFILE] == "xceed-7ce720fd-919c-11e9-846c-06b12363b88e"
    assert {"house", "tech_house", "techno"} <= set(_genres(event))


def test_secret_venue_until_late_has_no_place_and_no_end() -> None:
    event = _event(252823)
    assert (event.venue_name, event.address, event.latitude, event.longitude) == (None, None, None, None)
    assert (event.city, event.country, event.end_at) == ("Valencia", "ES", None)
    assert VENUE_PROFILE not in event.extra


def test_outside_the_eurozone_the_city_currency_and_a_closed_sale_has_no_price() -> None:
    event = _event(251856)
    assert (event.country, event.city, event.currency, event.min_price) == ("ZA", "Cape Town", "ZAR", None)
    assert (
        event.start_at is not None
        and event.start_at.utcoffset() == datetime(2026, 11, 18, tzinfo=ZoneInfo("Africa/Johannesburg")).utcoffset()
    )


def test_concert_vibes_are_event_types() -> None:
    event = _event(248797)
    assert event.event_types == ["concert", "live show"]
    assert event.source_genres == ["rap", "hip-hop", "tech house"]
    assert {"live_show", "hip_hop"} <= set(_genres(event))


def test_private_event_is_dropped_and_a_missing_detail_still_gives_the_event() -> None:
    item = ITEMS[242783]
    assert parse_event(item, {"settings": {"isPrivate": True}}, None, S, TZ) is None
    bare = parse_event(item, {}, None, S, TZ)
    assert bare is not None and bare.description is None and bare.min_price is None and bare.latitude == 41.387721


def test_artist_profiles_skip_generic_entries_and_keep_photo_and_links() -> None:
    lineup = ITEMS[250782]["lineup"] + [{"name": "TBA", "slug": "tba", "isGeneric": True}]
    profiles = artist_profiles(lineup, S.placeholder)
    assert [p.name for p in profiles] == ["Mattia Gasparotto", "Gabriele Goduti", "Pietro Ottonello"]
    mattia, gabriele = profiles[0], profiles[1]
    assert mattia.spotify_url == "https://open.spotify.com/artist/401zsBg88IYDg1x0akeqO4"
    assert mattia.image_url and mattia.image_url.startswith("https://images.xceed.me/artists/covers/")
    assert mattia.profile_url == "https://xceed.me/en/artist/mattia-gasparotto-2"
    assert gabriele.links == ["https://soundcloud.com/user630755595", "https://www.subvrs.it/artists"]
    assert all(p.source == "xceed" for p in profiles)


def test_venue_profile_from_its_api_page() -> None:
    page = unwrap(_load("venue.json"))
    profile = parse_venue(ITEMS[242783]["venue"] | page, S)
    assert profile is not None
    assert profile.key == "xceed-7ce720fd-919c-11e9-846c-06b12363b88e"
    assert (profile.name, profile.address) == ("City Hall", "Rambla de Catalunya, 4")
    assert (profile.latitude, profile.longitude) == (41.387721, 2.168269)
    assert profile.about and profile.about.startswith("City Hall is located in the heart of Barcelona")
    assert profile.profile_url == "https://xceed.me/en/barcelona/venue/city-hall"
    assert profile.logo_url == page["coverUrl"]
    assert parse_venue({"name": "No id"}, S) is None


def test_every_venue_is_due_once_per_cycle() -> None:
    items = [{"venue": {"id": f"v{n}", "slug": f"s{n}"}} for n in range(60)] + [
        {"venueIsSecret": True, "venue": {"id": "x"}}
    ]
    hours = S.venue_slot_hours * 3600
    seen = [
        v["id"]
        for slot in range(S.venue_cycle)
        for v in venues_due(items, slot * hours + 1, S.venue_cycle, S.venue_slot_hours)
    ]
    assert sorted(seen) == sorted(f"v{n}" for n in range(60))
    due = venues_due(items, 5, S.venue_cycle, S.venue_slot_hours)
    assert [v["id"] for v in due] == sorted(
        f"v{n}" for n in range(60) if zlib.crc32(f"v{n}".encode()) % S.venue_cycle == 0
    )


# Every Xceed music genre (its /v1/music-genre-groups, Nov 2026) -> a site genre it must give.
MUSIC = {
    "breaks": ("Breaks", "bass_dubstep"), "drum-and-bass": ("Drum and Bass", "drum_and_bass"),
    "dub": ("Dub", "dancehall_reggae"), "dubstep": ("Dubstep", "bass_dubstep"),
    "garage-bassline": ("Garage / Bassline", "bass_dubstep"), "jungle": ("Jungle", "drum_and_bass"),
    "cumbia": ("Cumbia", "reggaeton_latin"), "dancehall": ("Dancehall", "dancehall_reggae"), "latin": ("Latin", "reggaeton_latin"),
    "reggae-dancehall": ("Reggae / Dancehall", "dancehall_reggae"), "reggaeton": ("Reggaeton", "reggaeton_latin"),
    "salsa-bachata": ("Salsa / Bachata", "salsa"), "samba-pagode": ("Samba / Pagode", "brazilian"),
    "dance": ("Dance", "edm"), "edm": ("EDM", "edm"), "eurodance": ("Eurodance", "pop"), "indie-dance": ("Indie Dance", "indie_dance"),
    "disco": ("Disco", "disco_funk"), "funk": ("Funk", "disco_funk"), "italo-disco": ("Italo Disco", "disco_funk"),
    "nu-disco-disco": ("Nu-Disco / Disco", "disco_funk"), "ebm": ("EBM", "dark_wave"), "electro": ("Electro", "electronic"),
    "indietronic": ("Indietronic", "electronic"), "new-wave": ("New Wave", "dark_wave"), "ambient": ("Ambient", "ambient"),
    "experimental": ("Experimental", "electronic"), "idm": ("IDM", "electronic"), "new-age": ("New Age", "downtempo_chill"),
    "hardcore": ("Hardcore", "hardstyle"), "hardstyle": ("Hardstyle", "hardstyle"), "rave": ("Rave", "electronic"),
    "uptempo": ("Uptempo", "hardstyle"), "acid-house": ("Acid House", "house"), "afro-house": ("Afro House", "afro_house"),
    "deep-house": ("Deep House", "deep_house"), "electronica-downtempo": ("Electronica / Downtempo", "downtempo_chill"),
    "house": ("House", "house"), "latin-tech-house": ("Latin Tech House", "tech_house"), "microhouse": ("Microhouse", "minimal_deep_tech"),
    "organic-house": ("Organic House", "organic_house"), "progressive": ("Progressive", "progressive_house"),
    "tech-house": ("Tech house", "tech_house"), "70s-80s": ("70s - 80s", "throwback"), "80-s": ("80's", "eighties"),
    "90s-00s": ("90s - 00s", "nineties"), "hits": ("Hits", "mainstream"), "indie-pop": ("Indie pop", "pop"), "pop": ("Pop", "pop"),
    "songwriting": ("Songwriting", "live_show"), "alternative": ("Alternative", "rock_indie"), "indie-rock": ("Indie - Rock", "rock_indie"),
    "metal-hard-rock": ("Metal / Hard Rock", "metal"), "psych-rock": ("Psych Rock", "rock_indie"), "punk": ("Punk", "punk"),
    "rock-and-roll": ("Rock-and-Roll", "rock_indie"), "amapiano": ("Amapiano", "amapiano"), "blues": ("Blues", "blues"),
    "folk": ("Folk", "world_balkan"), "jazz-blues": ("Jazz / Blues", "jazz"), "soul": ("Soul", "rnb"),
    "acid-techno": ("Acid Techno", "techno"), "bounce": ("Bounce", "techno"), "hard-techno": ("Hard Techno", "hard_techno"),
    "hardgroove": ("Hardgroove", "hard_techno"), "industrial": ("Industrial", "hard_techno"),
    "melodic-house-techno": ("Melodic House & Techno", "melodic_techno"), "minimal": ("Minimal", "minimal_deep_tech"),
    "schranz": ("Schranz", "hard_techno"), "techno": ("Techno", "techno"), "tekno": ("Tekno", "techno"),
    "psytrance": ("Psytrance", "psytrance"), "trance": ("Trance", "trance"), "afrobeats": ("Afrobeats", "afro"),
    "hip-hop": ("Hip-hop", "hip_hop"), "r-b": ("R&B", "rnb"), "rap": ("Rap", "hip_hop"), "trap": ("Trap", "hip_hop"),
}  # fmt: skip
# Every vibe (/v1/vibes) that names a kind of event -> its site genre; the others ("Elegant", "Local") say
# nothing on their own and leave the music genres, or the club-night default, to place the event.
VIBES = {
    "beach-party": ("beach-party", "nightclubs"), "boat-party": ("Boat Party", "nightclubs"),
    "chill-and-relax": ("Chill & Relax", "downtempo_chill"), "concert": ("Concert", "live_show"),
    "latino": ("Latino", "reggaeton_latin"), "lgbtq": ("LGBTQ+", "lgbt"), "literature-poetry": ("Literature & Poetry", "literature"),
    "live-music": ("Live Music", "live_show"), "live-show": ("Live Show", "live_show"), "pool-party": ("Pool Party", "pool_party"),
    "rooftop-party": ("Rooftop Party", "nightclubs"), "sport": ("Sport", "sport"), "student": ("Student", "students"),
    "theatre-comedy": ("Theatre & Comedy", "theatre"), "theme-party": ("Theme Party", "nightclubs"),
    "underground": ("Underground", "electronic"), "afterwork": ("Afterwork", "happy_hour"),
}  # fmt: skip


def _typed(music: list[dict[str, str]], vibes: list[dict[str, str]], **detail: Any) -> ScrapedEvent:
    item = ITEMS[242783] | {"name": "Zzz", "musicGenres": music}
    event = parse_event(item, {"vibes": vibes, **detail}, None, S, TZ)
    assert event is not None
    return event


@pytest.mark.parametrize("slug", sorted(MUSIC))
def test_each_music_genre_gives_its_site_genre(slug: str) -> None:
    name, genre = MUSIC[slug]
    genres = _genres(_typed([{"name": name, "slug": slug}], []))
    assert genre in genres
    assert "dance" not in genres  # stage dance (ballet) is never a club genre


@pytest.mark.parametrize("slug", sorted(VIBES))
def test_each_event_type_vibe_gives_its_site_genre(slug: str) -> None:
    name, genre = VIBES[slug]
    assert genre in _genres(_typed([], [{"name": name, "slug": slug}]))


def test_festival_flag_default_club_night_and_place_only_vibes() -> None:
    assert "festival" in _genres(_typed([], [], isFestival=True))
    plain = _typed([], [{"name": "Elegant", "slug": "elegant"}, {"name": "Food & Drinks", "slug": "food-drinks"}])
    assert (plain.source_genres, plain.event_types) == (["nightlife"], ["elegant"])
    assert _genres(plain) == ["nightclubs"]
    concert = genres_of({"musicGenres": []}, {"vibes": [{"name": "Concert", "slug": "concert"}]}, S)
    assert concert == ([], ["concert"])  # a show vibe: no club-night default


def _future(payload: dict[str, Any]) -> dict[str, Any]:
    """The fixture's events moved so the first starts in two days: upcoming, and not too far ahead."""
    shift = int(datetime.now(UTC).timestamp()) + 2 * 86400 - min(i["startingTime"] for i in payload["data"])
    data = [
        i | {"startingTime": i["startingTime"] + shift, "endingTime": i["endingTime"] + shift} for i in payload["data"]
    ]
    return payload | {"data": data}


def test_a_run_reads_every_page_the_sales_the_details_and_this_slots_venues() -> None:
    scraper = XceedScraper(
        XceedSettings(cron="1 6,18 * * *", page_size=3, venue_cycle=1, http={"request_delay_seconds": 0}),
        HttpSettings(),
        TZ,
    )
    listing = _future(LISTING)["data"]
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path, params = request.url.path, request.url.params
        asked.append(path)
        if path == "/v1/events":
            assert "cities" not in str(request.url) and int(params["startTime"]) > 0
            offset = int(params["offset"])
            return httpx.Response(200, json={"success": True, "data": listing[offset : offset + int(params["limit"])]})
        if path.startswith("/v1/events/"):
            return httpx.Response(200, json={"success": True, "data": DETAILS[path.rsplit("/", 1)[1]]})
        if path == "/v1/offers/sales":
            ids = params.get_list("eventIds[]")
            assert 0 < len(ids) <= 25
            return httpx.Response(200, json={"success": True, "data": [SALES[i] for i in ids if i in SALES]})
        if path.startswith("/v1/venues/"):
            return httpx.Response(200, json=_load("venue.json"))
        if path.startswith("/clubs/covers/"):
            return httpx.Response(200, content=b"picture")
        return httpx.Response(404)

    async def go() -> list[ScrapedEvent]:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return [e async for e in scraper.scrape()]

    events = asyncio.run(go())
    assert sorted(e.source_event_id for e in events) == sorted(str(i) for i in ITEMS)
    # 3 + 2: the short page ends the listing (pages go in windows of `concurrency`, so up to 3 more are empty)
    assert 2 <= asked.count("/v1/events") <= 2 + 3
    assert asked.count("/v1/offers/sales") == 1
    assert len(scraper.venue_profiles) == 4  # every non-secret venue, cycle 1
    assert all(p.logo == b"picture" for p in scraper.venue_profiles)
    assert not scraper.failures
    assert min(e.start_at for e in events if e.start_at) > datetime.now(UTC)
