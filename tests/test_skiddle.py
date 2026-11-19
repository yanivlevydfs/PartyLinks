"""Skiddle: city pages' and the API's rows to ScrapedEvent, the city rotation, artists and venues."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta
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
from partylinks.scrapers.base import SourceBlockedError
from partylinks.scrapers.skiddle import (
    SkiddleScraper,
    SkiddleSettings,
    artist_pages,
    city_page,
    is_challenge,
    parse_event,
    rotation,
    sitemap_cities,
    venue_page,
    venue_profile,
)
from partylinks.venue_profiles import VENUE_PROFILE

TZ = "Asia/Jerusalem"
LONDON = ZoneInfo("Europe/London")
FIXTURE = Path(__file__).parent / "fixtures" / "skiddle" / "whats_on_manchester.html"
ROW = {
    "id": "42256518",
    "EventCode": "CLUB",
    "eventname": "Groove Odyssey 17TH Birthday",
    "cancelled": "0",
    "venue": {"id": 343, "name": "Fabric London", "address": "77A Charterhouse St, London", "town": "London", "postcode_lookup": "London", "postcode": "EC1M 6HJ", "country": "GB", "latitude": 51.519566, "longitude": -0.102463, "type": "Nightclub"},
    "imageurl": "https://d31fr2pwly4c4s.cloudfront.net/e/6/5/2226833_e6a33e56_groove-odyssey-17th-birthday_th.jpg",
    "xlargeimageurl": "https://d31fr2pwly4c4s.cloudfront.net/e/6/5/2226833_e6a33e56_groove-odyssey-17th-birthday_1024.jpg",
    "link": "https://www.skiddle.com/whats-on/London/Fabric-London/Groove-Odyssey-17TH-Birthday/42256518/",
    "startdate": "2027-06-12T14:00:00+00:00",
    "enddate": "2027-06-12T22:00:00+00:00",
    "description": "Join us in celebrating our 17th birthday with a deep house and soulful lineup at Fabric London!",
    "minage": "18",
    "ticketpricing": {"minPrice": 12.5, "maxPrice": 20},
    "ticketUrl": "",
    "currency": "GBP",
    "eventCurrency": "GBP",
    "ticketStatusText": "Buy Tickets",
    "artists": [{"artistid": "123570548", "name": "Natasha Diggs", "image": "https://i.scdn.co/image/ab67", "spotifyartisturl": "spotify:artist:6H2JQ8igAAa5UBr2u496mb"}],
}  # fmt: skip


def _settings(**kw: Any) -> SkiddleSettings:
    return SkiddleSettings(cron="0 0 * * *", **kw)


def _genres(event: ScrapedEvent) -> list[str]:
    settings = get_settings()
    enriched = Enricher(settings.enrichment, settings.timezone).enrich(event)
    assert enriched is not None
    return enriched.genres


def test_city_page_reads_every_category_and_the_nearest_venues() -> None:
    rows, venues, counts = city_page(FIXTURE.read_text(encoding="utf-8"))
    # Ten categories, two rows each in the fixture (the live page has 4-8 each, ~70 in all).
    assert len(rows) == 20 and len(venues) == 2
    assert counts["clubs"] == 344 and counts["gigs"] == 378 and len(counts) == 10
    codes = {"CLUB", "LIVE", "FEST", "COMEDY", "THEATRE", "FILM", "EXPER", "FOOD", "KIDS", "SPORT", "SEASONAL"}
    assert {r["EventCode"] for r in rows} >= codes
    with pytest.raises(ParseError):
        city_page('<script id="__NEXT_DATA__" type="application/json">{"props": {"pageProps": {}}}</script>')


def test_a_category_page_still_reads() -> None:
    data = {"props": {"pageProps": {"initialResults": {"events": [ROW, {"eventname": "no id"}], "totalCount": "551"}}}}
    page = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'
    rows, venues, counts = city_page(page)
    assert [r["id"] for r in rows] == ["42256518"] and venues == [] and counts == {"results": 551}


def test_every_fixture_row_parses_with_a_place_and_a_genre() -> None:
    rows, _, _ = city_page(FIXTURE.read_text(encoding="utf-8"))
    for row in rows:
        event = parse_event(row, _settings(), TZ, "Manchester")
        assert event is not None and event.city and event.country == "GB" and event.time_zone == "Europe/London"
        assert event.latitude is not None or event.address
        assert _genres(event), row["eventname"]


def test_row_in_the_venue_clock_with_coordinates_price_and_currency() -> None:
    links = {"123570548": "https://www.skiddle.com/artists/natasha-diggs-123570548/"}
    event = parse_event(ROW, _settings(), TZ, None, links)
    assert event is not None
    # +00:00 on the row is the local clock: 14:00 in London in June is 13:00 UTC.
    assert event.start_at == datetime(2027, 6, 12, 14, tzinfo=LONDON)
    assert event.end_at == datetime(2027, 6, 12, 22, tzinfo=LONDON)
    assert (event.country, event.time_zone, event.city) == ("GB", "Europe/London", "London")
    assert (event.venue_name, event.address) == ("Fabric London", "77A Charterhouse St, London, EC1M 6HJ")
    assert (event.latitude, event.longitude) == (51.519566, -0.102463)
    assert (event.min_price, event.max_price, event.currency) == (Decimal("12.5"), Decimal(20), "GBP")
    assert event.images == [ROW["xlargeimageurl"]]
    assert event.min_age == 18 and not event.is_sold_out
    assert event.extra == {VENUE_PROFILE: "skiddle-343"}
    assert (event.source_genres, event.event_types) == (["nightlife"], ["club"])
    assert event.artists == ["Natasha Diggs"]
    profile = event.artist_profiles[0]
    assert profile.spotify_url == "https://open.spotify.com/artist/6H2JQ8igAAa5UBr2u496mb"
    assert profile.profile_url == links["123570548"] and profile.image_url == "https://i.scdn.co/image/ab67"
    assert "nightclubs" in _genres(event)


@pytest.mark.parametrize(
    ("code", "genre"),
    [
        ("CLUB", "nightclubs"), ("BARPUB", "nightclubs"), ("LIVE", "live_show"), ("FEST", "festival"),
        ("COMEDY", "stand_up"), ("THEATRE", "theatre"), ("DANCE", "dance"), ("ARTS", "culture"),
        ("BOOKS", "lectures"), ("FILM", "cinema"), ("DRIVEIN", "cinema"), ("EXHIB", "exhibitions"),
        ("EXPER", "attractions"), ("KIDS", "kids"), ("FOOD", "food"), ("SPORT", "sport"),
        ("HEALTH", "yoga_wellness"), ("SEASONAL", "holidays"), ("DATE", "singles_dating"), ("LGB", "lgbt"),
        ("NEWCODE", "culture"),
    ],
)  # fmt: skip
def test_each_event_type_lands_in_its_genre(code: str, genre: str) -> None:
    # A title and text that name nothing: the type alone places it.
    row = ROW | {"EventCode": code, "eventname": "Saturday 12th", "description": None, "artists": []}
    event = parse_event(row, _settings(), TZ)
    assert event is not None
    assert genre in _genres(event)


def test_the_api_genres_join_the_type() -> None:
    row = ROW | {"genres": [{"genreid": "8", "name": "House"}, {"genreid": "9", "name": "Techno"}]}
    event = parse_event(row, _settings(), TZ)
    assert event is not None and event.source_genres == ["nightlife", "house", "techno"]
    assert {"nightclubs", "house", "techno"} <= set(_genres(event))


def test_cancelled_sold_out_seller_link_and_street_town() -> None:
    assert parse_event(ROW | {"cancelled": "1"}, _settings(), TZ) is None
    other = ROW | {
        "ticketStatusText": "Sold Out",
        "ticketUrl": "https://seller.example/t/1",
        "enddate": "2027-06-12T14:00:00+00:00",
    }
    event = parse_event(other, _settings(), TZ)
    assert event is not None and event.is_sold_out and event.end_at is None
    assert event.extra["purchase_url"] == "https://seller.example/t/1"
    street_town = ROW | {"venue": ROW["venue"] | {"town": "1 Sports City Way"}}
    event = parse_event(street_town, _settings(), TZ, "Manchester")
    assert event is not None and event.city == "Manchester"
    with pytest.raises(ParseError):
        parse_event(ROW | {"startdate": None}, _settings(), TZ)


def test_irish_venue_in_dublin_time_and_euros() -> None:
    row = ROW | {"venue": ROW["venue"] | {"town": "Dublin", "country": "IE"}, "eventCurrency": None, "currency": None}
    event = parse_event(row, _settings(), TZ)
    assert event is not None and (event.country, event.time_zone, event.currency) == ("IE", "Europe/Dublin", "EUR")


def test_nearest_venue_is_a_profile() -> None:
    _, venues, _ = city_page(FIXTURE.read_text(encoding="utf-8"))
    profile = venue_profile(venues[0], None)
    assert profile is not None
    assert (profile.key, profile.name, profile.source) == ("skiddle-17925", "Company Bar", "skiddle")
    assert profile.about and profile.about.startswith("COMPANY BAR") and "Type: Bar/Pub" in profile.about
    assert profile.address == "28 Richmond Street, Manchester, M1 3NB"
    assert (profile.latitude, profile.longitude) == (53.4773205, -2.2371776)
    assert profile.logo_url == "https://dhzjvxyl79yzn.cloudfront.net/5/17925.jpg"
    # A bare row venue (no about, no picture) is no profile.
    assert venue_profile(ROW["venue"], ROW["link"]) is None
    assert venue_page(ROW["link"]) == "https://www.skiddle.com/whats-on/London/Fabric-London/"
    assert venue_page("https://www.skiddle.com/festivals/x/") is None


def test_rotation_walks_every_city_over_a_cycle() -> None:
    cities = [f"c{i}" for i in range(10)]
    assert rotation(cities, 4, 0) == ["c0", "c1", "c2", "c3"]
    assert rotation(cities, 4, 2) == ["c8", "c9", "c0", "c1"]
    assert {c for slot in range(3) for c in rotation(cities, 4, slot)} == set(cities)
    assert rotation(cities, 20, 5) == cities and rotation([], 4, 1) == []


def test_sitemaps() -> None:
    xml = "".join(
        f"<url><loc>https://www.skiddle.com/{p}</loc></url>"
        for p in ("whats-on/", "whats-on//", "whats-on/London/", "whats-on/London/whats-on-in-london-today/", "clubs/Leeds/", "whats-on/Leeds/", "whats-on/London/")
    )  # fmt: skip
    assert sitemap_cities(xml, "https://www.skiddle.com") == ["London", "Leeds"]
    artists = "<loc>https://www.skiddle.com/artists/-mark-nightingale-123498550/</loc><loc>https://www.skiddle.com/artists/x/</loc>"
    assert artist_pages(artists) == {"123498550": "https://www.skiddle.com/artists/-mark-nightingale-123498550/"}


def test_waf_challenge_is_seen_but_never_handed_to_the_proxy_failover() -> None:
    challenge = httpx.Response(202, headers={"x-amzn-waf-action": "challenge"})
    assert is_challenge(challenge) and not is_challenge(httpx.Response(200, text="<html></html>"))
    assert not SkiddleScraper.is_blocked(SkiddleScraper.__new__(SkiddleScraper), challenge)


def _future(page: str) -> str:
    """Every fixture row a month from now, so it stays upcoming (and within retention's horizon)."""
    day = (datetime.now(LONDON) + timedelta(days=30)).date().isoformat()
    page = re.sub(r'"(startdate|enddate)": "[^"]*"', rf'"\1": "{day}T20:00:00+00:00"', page)
    return re.sub(r'"date": "[^"]*"', f'"date": "{day}"', page)


def _scraper(**kw: Any) -> SkiddleScraper:
    http = {"request_delay_seconds": 0, "backoff_seconds": 0} | kw.pop("http", {})
    settings = _settings(page_gap_seconds=0, http=http, **kw)
    return SkiddleScraper(settings, HttpSettings(), TZ)


def _run(scraper: SkiddleScraper, handler: Any) -> list[ScrapedEvent]:
    async def go() -> list[ScrapedEvent]:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return [e async for e in scraper.scrape()]

    return asyncio.run(go())


def test_a_run_reads_its_rotation_slot_and_stops_at_the_first_challenge() -> None:
    page = _future(FIXTURE.read_text(encoding="utf-8"))
    asked: list[str] = []
    sitemap = "".join(f"<loc>https://www.skiddle.com/whats-on/{c}/</loc>" for c in ("A", "B", "C"))

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path.endswith("general.xml"):
            return httpx.Response(200, text=sitemap)
        if request.url.path.endswith("artists1.xml"):
            return httpx.Response(200, text="")
        if request.url.host.startswith("dhzjvxyl79yzn"):
            return httpx.Response(200, content=b"img", headers={"content-type": "image/jpeg"})
        if len([p for p in asked if p.startswith("/whats-on/")]) > 1:
            return httpx.Response(202, headers={"x-amzn-waf-action": "challenge"})
        return httpx.Response(200, text=page)

    scraper = _scraper(pages_per_run=3, menu_cities=[], rotation_hours=1e9)  # slot 0: A, B, C
    events = _run(scraper, handler)
    # A read, B challenged: the run stops there (C never asked), keeping A's rows and venues.
    assert [p for p in asked if p.startswith("/whats-on/")] == ["/whats-on/A/", "/whats-on/B/"]
    assert len(events) == 20 and not scraper.failures
    assert len(scraper.venue_profiles) == 2 and scraper.venue_profiles[0].key == "skiddle-17925"
    assert all(p.logo == b"img" for p in scraper.venue_profiles)


def test_a_run_challenged_on_its_first_page_is_blocked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host.endswith("cloudfront.net"):
            return httpx.Response(200, text="")
        return httpx.Response(202, headers={"x-amzn-waf-action": "challenge"})

    with pytest.raises(SourceBlockedError):
        _run(_scraper(menu_cities=["London"]), handler)


def test_the_api_pages_through_the_whole_catalogue() -> None:
    day = (datetime.now(LONDON) + timedelta(days=30)).date().isoformat()
    later = ROW | {"startdate": f"{day}T14:00:00+00:00", "enddate": f"{day}T22:00:00+00:00"}
    venue = later["venue"] | {"description": "A club.", "imageUrl": "https://img.example/v.jpg"}
    rows = [later | {"id": str(i), "venue": venue} for i in range(5)]
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host.endswith("cloudfront.net") or request.url.host == "img.example":
            return httpx.Response(200, text="")
        seen.append(request)
        offset = int(request.url.params["offset"])
        return httpx.Response(200, json={"error": 0, "totalcount": "5", "results": rows[offset : offset + 2]})

    scraper = _scraper(api_key="k", api_page_size=2, venue_logos=False)
    events = _run(scraper, handler)
    assert [e.source_event_id for e in events] == ["0", "1", "2", "3", "4"]
    assert [r.url.params["offset"] for r in seen] == ["0", "2", "4"]
    first = seen[0]
    assert first.url.path == "/api/v1/events/search/" and first.url.params["api_key"] == "k"
    assert first.url.params["description"] == "1" and first.url.params["minDate"]
    assert first.headers["user-agent"].startswith("SPOTX/")
    assert [p.key for p in scraper.venue_profiles] == ["skiddle-343"]
    assert scraper.venue_profiles[0].profile_url == "https://www.skiddle.com/whats-on/London/Fabric-London/"


def test_the_api_error_fails_the_run() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host.endswith("cloudfront.net"):
            return httpx.Response(200, text="")
        return httpx.Response(200, json={"error": 1, "errormessage": "A valid API Key must be provided"})

    with pytest.raises(ParseError, match="valid API Key"):
        _run(_scraper(api_key="bad"), handler)


def test_the_api_key_comes_from_the_environment_beside_the_yaml_block(monkeypatch: pytest.MonkeyPatch) -> None:
    from partylinks.config import Settings
    from partylinks.scrapers import build_scraper

    monkeypatch.setenv("PARTYLINKS_SOURCES__SKIDDLE__API_KEY", "from-env")
    scraper = build_scraper("skiddle", Settings())
    assert isinstance(scraper, SkiddleScraper)
    assert scraper.settings.api_key == "from-env" and scraper.settings.pages_per_run == 5
    monkeypatch.delenv("PARTYLINKS_SOURCES__SKIDDLE__API_KEY")
    off = build_scraper("skiddle", Settings())
    assert isinstance(off, SkiddleScraper) and off.settings.api_key is None and not off.settings.unlist_missing


def test_a_refusal_fails_once_on_the_same_ip_and_ends_the_run() -> None:
    scraper = SkiddleScraper.__new__(SkiddleScraper)
    assert scraper.is_gone(httpx.Response(403)) and scraper.is_gone(httpx.Response(429))
    assert not scraper.is_gone(httpx.Response(404)) and not scraper.is_gone(httpx.Response(200))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host.endswith("cloudfront.net"):
            return httpx.Response(200, text="")
        return httpx.Response(403)

    with pytest.raises(SourceBlockedError, match="refused"):
        _run(_scraper(menu_cities=["London"], http={"max_retries": 0}), handler)


def test_a_gone_venue_picture_is_no_failure() -> None:
    page = _future(FIXTURE.read_text(encoding="utf-8"))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host.startswith("dhzjvxyl79yzn"):
            return httpx.Response(403)
        if request.url.host.endswith("cloudfront.net"):
            return httpx.Response(200, text="")
        return httpx.Response(200, text=page)

    scraper = _scraper(menu_cities=["Manchester"])
    assert len(_run(scraper, handler)) == 20
    assert not scraper.failures and all(p.logo is None for p in scraper.venue_profiles)
