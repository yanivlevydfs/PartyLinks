"""GETIN: filter-events rows, getin.xyz event / producer / artist pages and the sitemap to ScrapedEvent,
artist profiles and venue profiles; GETIN's categories to our genres."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from functools import cache
from pathlib import Path

import httpx
import pytest

from partylinks.artists import ArtistProfile
from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.models import ScrapedEvent
from partylinks.scrapers import getin
from partylinks.scrapers.getin import (
    GetinScraper,
    GetinSettings,
    address_country,
    category_list,
    event_from_page,
    genre_labels,
    merge_rows,
    page_config,
    parse_artist_page,
    parse_row,
    performers,
    producer_events,
    sitemap_locs,
    venue_of,
    with_page,
)
from partylinks.venue_profiles import VENUE_PROFILE

TZ = "Asia/Jerusalem"
XYZ = "https://getin.xyz"
S = GetinSettings(cron="42 7,19 * * *")
FIXTURES = Path(__file__).parent / "fixtures/getin"
CATS = {
    c["id"]: c["name"]
    for c in category_list({"data": [{"id": 9, "name": {"en": "Techno"}}, {"id": 1, "name": {"en": "Nightlife"}}]})
}
# The day the fixtures were saved: their events are upcoming then.
NOW = datetime(2026, 11, 19, 8, tzinfo=UTC)

OLD = {
    "event_id": 425771, "title": "unKommon events Presents | Guy Gerber | Rumors", "start_date": "2026-12-02 20:00:00",
    "end_date": "2026-12-03 03:00:00", "time_zone": "America/New_York", "address": "300 NE 71st St, Miami, FL 33138, USA",
    "lat": "25.8403145", "lon": "-80.19", "min_price": "40.00", "max_price": "225.00", "country_short_name": "US",
    "city": "Miami", "categories": [1, 9], "file": "_cover_20260819_162449_425771.png", "producer_name": "Rumors",
    "artists": [{"name": "Guy Gerber", "image": "https://i.scdn.co/image/a", "genres": ["techno melódico"]}], "is_new_platform": False,
}  # fmt: skip
NEW = {
    "event_id": 41328, "title": "Roger Sanchez - Nov 20 inside FUTURE", "start_date": "2026-11-20 22:00:00",
    "end_date": "2026-11-21 04:30:00", "time_zone": "America/Toronto", "country_short_name": "CA", "city": "Toronto",
    "min_price": 35, "max_price": 35, "categories": [], "artists": [], "is_new_platform": True,
    "new_platform_url": "https://getin.xyz/event/41328?seller_code=getin",
    "new_platform_images": {"desktop": "https://static.getin.xyz/gallery/desktop_cover_41328.png"},
    "venue": {"name": "Future Nightlife", "address": "570 Queen St W, Toronto", "lat": "43.64", "lon": "-79.40"},
}  # fmt: skip
ISRAEL = {"event_id": 7, "title": "מסיבת גג", "start_date": "2026-12-10 23:00:00", "end_date": "", "country_short_name": "IL",
          "city": "הרצליה", "address": "מרטין בובר 6, הרצליה, ישראל", "lat": "32.16", "lon": "34.84", "categories": [9]}  # fmt: skip


def _page(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _json(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@cache
def _enricher() -> Enricher:
    settings = Settings()
    return Enricher(settings.enrichment, settings.timezone)


def _genres(source_genres: list[str], title: str = "Saturday") -> set[str]:
    event = ScrapedEvent(
        source="getin", source_event_id="1", url=f"{XYZ}/event/1", title=title, country="US",
        time_zone="America/New_York", city="Miami", source_genres=source_genres,
        start_at=datetime(2027, 1, 9, 22, tzinfo=getin.zone("America/New_York")),
    )  # fmt: skip
    enriched = _enricher().enrich(event)
    assert enriched is not None
    return set(enriched.genres)


def test_rows_merged_once_per_id() -> None:
    rows = merge_rows([], {"events": [OLD, NEW]})
    assert [r["event_id"] for r in merge_rows(rows, {"events": [NEW, ISRAEL]})] == [425771, 41328, 7]


def test_old_platform_event_abroad_in_its_zone_priced_once_the_page_names_the_currency() -> None:
    e = parse_row(OLD, CATS, S, TZ)
    assert e is not None and str(e.url) == "https://get-in.com/en/425771" and e.country == "US"
    assert e.time_zone == "America/New_York" and e.start_at.isoformat() == "2026-12-02T20:00:00-05:00"
    assert e.images == ["https://static.get-in.com/gallery/tablet_cover_20260819_162449_425771.png"]
    assert e.source_genres == ["nightlife", "techno", "techno melódico"] and e.artists == ["Guy Gerber"]
    assert e.min_price is None  # no currency yet: never priced as shekels
    page = '<script id="ng-state" type="application/json">{"event_details": {"currency": "USD"}}</script>'
    priced = with_page(e, page)
    assert priced.currency == "USD" and priced.min_price == Decimal(40) and priced.max_price == Decimal(225)
    assert "price_range" not in priced.extra


def test_new_platform_page_without_seller_code_and_default_genre() -> None:
    e = parse_row(NEW, CATS, S, TZ)
    assert e is not None and str(e.url) == "https://getin.xyz/event/41328"
    assert e.venue_name == "Future Nightlife" and (e.latitude, e.longitude) == (43.64, -79.40)
    assert e.images == ["https://static.getin.xyz/gallery/desktop_cover_41328.png"] and e.source_genres == ["nightlife"]


def test_israeli_night_has_no_country_and_no_placeholder_end() -> None:
    e = parse_row(ISRAEL, CATS, S, TZ)
    assert e is not None and e.country is None and e.time_zone is None and e.end_at is None
    assert e.start_at.isoformat() == "2026-12-10T23:00:00+02:00" and e.city == "הרצליה"


# ------------------------------------------------------------------ categories
# Every GETIN category (api/categories, Nov 2026; getin.xyz explore ids in brackets) -> our genres.
CATEGORY_GENRES = {
    "Techno": {"techno"},  # techno
    "Pacha": {"nightclubs"},  # pacha: Pacha's club nights
    "Melodic": {"melodic_techno"},  # melodic
    "House": {"house"},  # house
    "Indie Dance": {"indie_dance"},  # indie-dance: not dance (ballet) nor indie rock
    "Travel destinations": set(),  # travel-destinations: where, not what - no party, no genre
    "Nightlife": {"nightclubs"},  # nightlife
    "Afro House": {"afro_house", "house"},  # afro-house
    "Festivals": {"festival"},  # festivals
    "Experiences": set(),  # experiences: no party, no genre
    "Live Shows": {"live_show"},  # live-shows: concerts
}


def test_the_fixture_holds_every_getin_category() -> None:
    assert {c["name"] for c in category_list(_json("categories.json"))} == set(CATEGORY_GENRES)


@pytest.mark.parametrize(("category", "genres"), CATEGORY_GENRES.items())
def test_each_getin_category_maps_to_its_genre(category: str, genres: set[str]) -> None:
    assert _genres(genre_labels([category], S)) == genres


def test_category_labels_lower_case_once_each() -> None:
    assert genre_labels(["Indie Dance", "Techno", "techno", " ", "House"], S) == ["indiedance", "techno", "house"]
    assert _genres(genre_labels(["afro tech"], S)) == {"afro_house", "house"}  # not hi-tech


def test_metal_artist_genres_are_no_club_nights_genre() -> None:
    row = {**NEW, "artists": [{"genres": ["metalcore", "christian metal", "melodic techno"]}]}
    e = parse_row(row, CATS, S, TZ)
    assert e is not None and e.source_genres == ["melodic techno"]


# ------------------------------------------------------------------ getin.xyz pages
def test_event_page_alone_in_the_venue_zone_with_its_categories_and_currency() -> None:
    e = event_from_page(_page("event_41499.html"), f"{XYZ}/event/41499?seller_code=getin", S, TZ)
    assert e is not None and e.source_event_id == "41499" and str(e.url) == f"{XYZ}/event/41499"
    assert e.start_at.isoformat() == "2027-01-20T21:00:00+07:00" and e.end_at.isoformat() == "2027-01-21T07:00:00+07:00"
    assert (e.country, e.time_zone, e.city, e.venue_name) == ("TH", "Asia/Bangkok", "Than sadet", "UTOPIA EVENTS")
    assert (e.latitude, e.longitude) == (9.7409336, 100.0517324) and e.address and e.address.endswith("Thailand")
    assert e.source_genres == ["travel destinations", "festivals", "experiences"]
    assert e.currency == "USD" and e.min_price == Decimal(60) and e.tickets[0].price == Decimal(60)
    assert e.organizer == "UTOPIA" and e.images == [
        "https://static.getin.xyz/gallery/desktop_cover_20261029_162255_41499.png"
    ]
    assert _genres(e.source_genres, e.title) >= {"festival"} and "nightclubs" not in _genres(e.source_genres, e.title)


def test_country_by_the_venue_zone_when_the_address_names_it_in_its_own_language() -> None:
    page = _page("event_15.html")
    assert address_country("Av. 8 d'Agost, 27, 07800 Eivissa, Illes Balears, España") == "ZZ"
    e = event_from_page(page, f"{XYZ}/event/15", S, TZ)
    assert e is not None and (e.country, e.time_zone) == ("ES", "Europe/Madrid")
    assert e.start_at.isoformat() == "2026-12-31T23:00:00+01:00" and e.source_genres == [
        "pacha",
        "nightlife",
        "experiences",
    ]
    assert performers(page, XYZ)[1] == ("Marco Carola", f"{XYZ}/artist/marco-carola")


def test_israeli_event_page_has_no_country() -> None:
    e = event_from_page(_page("event_41596.html"), f"{XYZ}/event/41596", S, TZ)
    assert e is not None and e.country is None and e.time_zone is None and e.city == "הרצליה"
    assert e.start_at.isoformat() == "2026-11-21T17:00:00+02:00" and e.source_genres == ["nightlife"]
    assert (
        address_country("Dubai - United Arab Emirates") == "AE" and address_country("Japan, 〒153-0051 Tokyo") == "JP"
    )


def test_page_config_skips_the_share_button_with_the_same_id() -> None:
    assert page_config(_page("event_41556.html")) == {
        "eventId": 41556, "title": "Surf Club: MIND AGAINST", "categories": ["Nightlife", "Experiences"], "currency": "AED",
    }  # fmt: skip
    assert page_config("<html></html>") == {}


def test_api_row_with_its_page_keeps_the_rows_categories_and_takes_the_currency() -> None:
    row = next(r for r in _json("filter_events.json")["events"] if r["event_id"] == 41556)
    names = {c["id"]: c["name"] for c in category_list(_json("categories.json"))}
    e = parse_row(row, names, S, TZ)
    # The row names no artist, but gives their genres.
    genres = ["nightlife", "experiences", "melodic techno", "melodic house", "techno", "minimal techno"]
    assert e is not None and e.source_genres == genres and e.artists == []
    e = with_page(e, _page("event_41556.html"), S)
    assert e.source_genres == genres and e.currency == "AED" and e.min_price == Decimal(100)
    assert e.start_at.isoformat() == "2026-11-21T20:00:00+04:00" and e.time_zone == "Asia/Dubai"


def test_page_categories_replace_the_default_genre_only() -> None:
    e = parse_row(NEW, CATS, S, TZ)
    assert e is not None and e.source_genres == ["nightlife"]
    page = '<script>self.__next_f.push([1,"1c:{\\"config\\":{\\"eventId\\":41328,\\"categories\\":[\\"Indie Dance\\"]}}"])</script>'
    assert with_page(e, page, S).source_genres == ["indiedance"]


def test_artist_page_profile() -> None:
    p = parse_artist_page(_page("artist_solomun.html"), f"{XYZ}/artist/solomun", "Solomun")
    assert p == ArtistProfile(
        name="Solomun", source="getin", genre="טכנו מלודי",
        image_url="https://i.scdn.co/image/ab6761610000e5ebbfbacb40dced09ead13a3fa4",
        spotify_url="https://open.spotify.com/artist/5wJK4kQAkVGjqM9x46KQOC", profile_url=f"{XYZ}/artist/solomun",
        links=["https://www.solomunmusic.com/", "https://www.instagram.com/solomun/", "https://www.facebook.com/SolomunMusic/"],
    )  # fmt: skip
    assert parse_artist_page("<html></html>", f"{XYZ}/artist/x", "X") is None


def test_sitemap_producers_and_artist_slugs_and_a_producers_upcoming_events() -> None:
    locs = sitemap_locs(_page("sitemap.xml"), XYZ)
    assert locs == {"producer": [f"{XYZ}/producer/utopia"], "artist": ["solomun", "monolink"]}
    assert producer_events(_page("producer_utopia.html"), XYZ) == [("2027-01-20T21:00:00", f"{XYZ}/event/41499")]


def test_venue_profile_from_the_row_else_from_the_page() -> None:
    row = next(r for r in _json("filter_events.json")["events"] if r["event_id"] == 41556)
    v = venue_of(row, "", S)
    assert v is not None and (v.key, v.name, v.profile_url) == (
        "getin-surf-club",
        "Surf Club",
        f"{XYZ}/venue/surf-club",
    )
    assert v.logo_url and v.logo_url.startswith("https://static.getin.xyz/venue_files/") and v.latitude == 25.1065625
    v = venue_of(f"{XYZ}/event/41499", _page("event_41499.html"), S)
    assert v is not None and (v.key, v.name, v.logo_url) == ("getin-utopia-events", "UTOPIA EVENTS", None)
    assert (v.latitude, v.longitude) == (9.7409336, 100.0517324) and v.address and v.address.endswith("Thailand")
    assert venue_of({"event_id": 1}, "", S) is None  # old platform: the row names no venue


# ------------------------------------------------------------------ one run
def _run(
    monkeypatch: pytest.MonkeyPatch, known: dict[str, ArtistProfile] | None = None
) -> tuple[GetinScraper, list[ScrapedEvent], list[str]]:
    monkeypatch.setattr(getin, "_now", lambda: NOW)
    fast = {"request_delay_seconds": 0, "backoff_seconds": 0, "concurrency": 4}
    scraper = GetinScraper(GetinSettings(cron="42 7,19 * * *", http=fast), Settings().http, TZ)
    scraper.known_artists = known or {}
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url, path = str(request.url), request.url.path
        asked.append(f"{request.url.host}{path}")
        if path == "/api/filter-events":
            return httpx.Response(200, json=_json("filter_events.json"))
        if path == "/api/categories":
            return httpx.Response(200, json=_json("categories.json"))
        if path == "/sitemap.xml":
            return httpx.Response(200, text=_page("sitemap.xml"))
        if path == "/producer/utopia":
            return httpx.Response(200, text=_page("producer_utopia.html"))
        if path in ("/event/41556", "/event/41499", "/event/41596"):
            return httpx.Response(200, text=_page(f"event_{path.rsplit('/', 1)[1]}.html"))
        if path == "/artist/monolink":
            return httpx.Response(200, text=_page("artist_solomun.html"))
        if "venue_files" in url:
            return httpx.Response(200, content=b"\x89PNG")
        return httpx.Response(404)

    async def go() -> list[ScrapedEvent]:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return [e async for e in scraper.events()]

    return scraper, asyncio.run(go()), asked


def test_a_run_reads_the_api_the_producer_pages_artists_and_venues(monkeypatch: pytest.MonkeyPatch) -> None:
    scraper, events, asked = _run(monkeypatch)
    by_id = {e.source_event_id: e for e in events}
    # The API's five rows and the producer page's one event the API left out, nearest first.
    assert set(by_id) == {"41556", "425771", "41581", "41596", "418639", "41499"}
    assert by_id["41499"].source_genres == ["travel destinations", "festivals", "experiences"]
    # Performers from the event page, each with its artist page (404 here: the bare profile keeps the link).
    surf = by_id["41556"]
    assert surf.artists == ["Mind Against", "KatyaGur", "KayteK", "Darmon"]
    assert surf.artist_profiles[0].profile_url == f"{XYZ}/artist/mind-against"
    assert surf.extra[VENUE_PROFILE] == "getin-surf-club" and surf.currency == "AED"
    # An old-platform artist found by its sitemap slug gets the page's profile, the listing's photo kept as fallback.
    mono = next(p for p in by_id["425771"].artist_profiles if p.name == "Monolink")
    assert mono.profile_url == f"{XYZ}/artist/monolink" and mono.spotify_url and mono.links
    guy = next(p for p in by_id["425771"].artist_profiles if p.name == "Guy Gerber")
    assert guy.profile_url is None and guy.image_url  # not in the sitemap: the listing's photo only
    venues = {v.key: v for v in scraper.venue_profiles}
    assert {"getin-surf-club", "getin-utopia-events", "getin-6-3", "getin-304-e-3rd-st"} <= set(venues)
    assert venues["getin-surf-club"].logo == b"\x89PNG"
    assert asked.count("getin.xyz/artist/mind-against") == 1 and "getin.xyz/artist/monolink" in asked
    assert not scraper.failures


def test_known_artist_pages_are_not_read_again(monkeypatch: pytest.MonkeyPatch) -> None:
    known = {"monolink": ArtistProfile(name="Monolink", source="getin", profile_url=f"{XYZ}/artist/monolink")}
    _, events, asked = _run(monkeypatch, known)
    assert "getin.xyz/artist/monolink" not in asked
    mono = next(p for e in events if e.source_event_id == "425771" for p in e.artist_profiles if p.name == "Monolink")
    assert mono.profile_url == f"{XYZ}/artist/monolink"
