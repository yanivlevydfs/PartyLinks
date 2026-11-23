from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from partylinks.config import HttpSettings, Settings
from partylinks.enrichment import Enricher
from partylinks.models import ScrapedEvent
from partylinks.scrapers import eventbrite

FIXTURES = Path(__file__).parent / "fixtures" / "eventbrite"
CFG = eventbrite.EventbriteSettings(cron="0 0 * * *")


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _genres(labels: list[str], title: str = "Zzq", source: str = "eventbrite_test") -> list[str]:
    """The site genres the enrichment gives to these category words (a neutral title, no source default)."""
    s = Settings()
    event = ScrapedEvent(
        source=source, source_event_id="1", url="https://www.eventbrite.com/e/x-1", title=title,
        start_at=datetime(2099, 1, 1, 20, tzinfo=UTC), source_genres=labels,
    )  # fmt: skip
    return Enricher(s.enrichment, s.timezone)._detect_genres(event)


def _tag(tag: str, name: str = "") -> dict[str, str]:
    return {"prefix": tag.split("/")[0], "tag": tag, "display_name": name}


def _sub(name: str) -> dict[str, str]:
    return {"prefix": "EventbriteSubCategory", "tag": "EventbriteSubCategory/1", "display_name": name}


def test_discovery_page_and_a_timed_out_search():
    found = eventbrite.search_results(_fixture("berlin_music_page2.html"))
    assert found is not None and eventbrite.page_count(found) == 21
    assert [r["name"][:16] for r in found["results"]] == ["Duo-Release-Show", "Ma Baker Party i", "PANDAjazz: Red C"]
    # Nov 2026: about half the pages answer after ~11 s with no results and no pagination.
    assert eventbrite.search_results(_fixture("search_timed_out.html")) is None
    assert eventbrite.search_results("<html>no data</html>") is None


def test_event_page_gives_lineup_price_organizer_and_place():
    found = eventbrite.search_results(_fixture("berlin_music_page2.html"))
    assert found is not None
    listed = found["results"][0]
    page = _fixture("event_lea_rosa.html")
    e = eventbrite.parse_event(listed, eventbrite.page_context(page), eventbrite.offer(page), CFG)
    assert e is not None
    assert e.title == "Duo-Release-Show // Lea Rosa X FRAGO"
    assert e.start_at is not None and e.start_at.isoformat() == "2026-11-20T19:00:00+01:00"
    assert e.end_at is not None and e.end_at.isoformat() == "2026-11-20T22:00:00+01:00"
    assert (e.venue_name, e.address, e.city) == ("Kulturfabrik Moabit", "Lehrter Str. 35, 10557 Berlin", "Berlin")
    assert (e.latitude, e.longitude, e.country, e.time_zone) == (52.5343647, 13.3596885, "DE", "Europe/Berlin")
    assert (e.currency, e.min_price, e.max_price, e.is_sold_out) == ("EUR", Decimal("0.0"), Decimal("25.69"), False)
    assert e.organizer == "NG-Booking" and e.source_genres == ["indie"]
    assert e.description and e.description.startswith("Mit der bloßen Hand")
    assert e.artists == ["Lea Rosa", "FRAGO"]
    profile = e.artist_profiles[0]
    assert (profile.name, profile.source) == ("Lea Rosa", "eventbrite")
    assert profile.image_url and profile.image_url.startswith("https://img.evbuc.com/")
    assert profile.links == ["https://www.lea-rosa.de/", "https://www.instagram.com/learosa.wav/"]
    assert e.extra == {"purchase_url": listed["url"], "venue_profile": "eventbrite-299990365"}
    s = Settings()
    enriched = Enricher(s.enrichment, s.timezone).enrich(e)
    assert enriched is not None and "rock_indie" in enriched.genres
    assert enriched.time_zone == "Europe/Berlin" and enriched.lineup == ["Lea Rosa", "FRAGO"]


def test_israeli_event_keeps_shekels_and_no_country():
    listed = {
        "id": "1", "name": "Startup Pitch & Networking", "url": "https://www.eventbrite.com/e/x-1",
        "start_date": "2026-11-21", "start_time": "17:00", "end_date": "2026-11-21", "end_time": "21:00",
        "timezone": "Asia/Jerusalem",
        "primary_venue": {"id": "9", "name": "Hub", "address": {"city": "Tel Aviv-Yafo", "country": "IL",
                                                                 "latitude": "32.09", "longitude": "34.78"}},
        "tags": [_tag("EventbriteCategory/101"), _tag("EventbriteFormat/10")],
    }  # fmt: skip
    deal = {"lowPrice": "42.0", "highPrice": "42.0", "priceCurrency": "ILS", "availability": "InStock"}
    e = eventbrite.parse_event(listed, {}, deal, CFG)
    assert e is not None
    assert (e.country, e.time_zone, e.currency, e.min_price, e.max_price) == (None, None, "ILS", 42, None)
    assert e.source_genres == ["meetups"]  # the format; the category adds the same
    assert e.start_at is not None and e.start_at.isoformat() == "2026-11-21T17:00:00+02:00"


def test_a_cancelled_or_unlisted_page_is_no_event():
    listed = {"id": "2", "name": "Show", "url": "https://www.eventbrite.com/e/x-2", "start_date": "2099-01-01",
              "timezone": "Europe/Paris", "primary_venue": {"name": "V", "address": {"country": "FR"}}}  # fmt: skip
    assert eventbrite.parse_event(listed, {"basicInfo": {"status": "canceled"}}, {}, CFG) is None
    assert eventbrite.parse_event(listed, {"basicInfo": {"isListed": False}}, {}, CFG) is None
    e = eventbrite.parse_event(listed, {"basicInfo": {"status": "live", "isFree": True}}, {}, CFG)
    assert e is not None and e.min_price == 0 and not e.time_known and e.country == "FR"


def test_listing_keeps_public_upcoming_events_at_a_place():
    now = datetime(2026, 11, 19, 12, tzinfo=UTC)
    base = {"id": "1", "url": "https://www.eventbrite.com/e/x-1", "start_date": "2026-11-20", "start_time": "20:00",
            "timezone": "Europe/London", "primary_venue": {"name": "V", "address": {"country": "GB"}}}  # fmt: skip
    assert eventbrite.listable(base, now)
    assert not eventbrite.listable({**base, "is_online_event": True}, now)
    assert not eventbrite.listable({**base, "is_cancelled": True}, now)
    assert not eventbrite.listable({**base, "is_protected_event": True}, now)  # password-protected: not public
    assert not eventbrite.listable({**base, "primary_venue": None}, now)  # place to be announced
    assert not eventbrite.listable({**base, "start_date": "2026-11-18"}, now)


# Each Eventbrite category, format and subcategory word -> the site genre it must give.
EXPECTED = {
    "EventbriteCategory/103": "live_show", "EventbriteCategory/104": "cinema", "EventbriteCategory/105": "culture",
    "EventbriteCategory/110": "food", "EventbriteCategory/116": "holidays", "EventbriteCategory/101": "meetups",
    "EventbriteCategory/102": "hi_tech", "EventbriteCategory/107": "yoga_wellness", "EventbriteCategory/108": "sport",
    "EventbriteCategory/115": "kids", "EventbriteFormat/5": "festival", "EventbriteFormat/11": "nightclubs",
    "EventbriteFormat/7": "cinema", "EventbriteFormat/9": "workshops", "EventbriteFormat/16": "tours",
    "theatre": "theatre", "musical": "musicals", "comedy": "stand_up", "dance": "dance", "ballet": "dance",
    "opera": "classical_opera", "orchestra": "classical_opera", "fine art": "exhibitions", "literary arts": "literature",
    "dj/dance": "electronic", "edm / electronic": "electronic", "hip hop / rap": "hip_hop", "r&b": "rnb", "jazz": "jazz",
    "blues & jazz": "jazz", "rock": "rock_indie", "indie": "rock_indie", "metal": "metal", "punk/hardcore": "punk",
    "pop": "pop", "k-pop": "kpop", "top 40": "mainstream", "latin": "reggaeton_latin", "reggae": "dancehall_reggae",
    "country": "country", "world": "world_balkan", "classical": "classical_opera", "singer/songwriter": "live_show",
    "film": "cinema", "gaming": "gaming", "wine": "tastings", "beer": "tastings", "food": "food", "lgbt": "lgbt",
}  # fmt: skip


@pytest.mark.parametrize(
    "key", [*CFG.tag_genres, *CFG.subcategory_genres], ids=lambda k: k.replace("/", "_").replace(" ", "_")
)
def test_every_mapped_category_gives_a_site_genre(key):
    labels = CFG.tag_genres.get(key) or CFG.subcategory_genres[key]
    genres = _genres(labels)
    assert genres, f"{key} -> {labels} gives no genre"
    if key in EXPECTED:
        assert EXPECTED[key] in genres, f"{key} -> {labels} -> {genres}"


def test_subcategory_beats_category_and_a_party_is_no_concert():
    film_comedy = [_tag("EventbriteCategory/104"), _sub("Comedy"), _tag("EventbriteFormat/6")]
    assert eventbrite.categories(film_comedy, CFG) == ["comedy"]  # stand-up, not cinema
    music_party = [_tag("EventbriteCategory/103"), _tag("EventbriteFormat/11")]
    assert eventbrite.categories(music_party, CFG) == ["party"]
    assert _genres(eventbrite.categories(music_party, CFG)) == ["nightclubs"]
    # The listing names no subcategory; its page does ("DJ/Dance").
    assert eventbrite.categories(music_party, CFG, "DJ/Dance") == ["party", "dj set"]
    assert eventbrite.categories([_tag("EventbriteCategory/103"), _sub("Other")], CFG) == ["music"]


def test_every_city_is_read_once_a_cycle():
    cities = [f"c{i}" for i in range(60)]
    start = datetime(2026, 11, 19, tzinfo=UTC)
    slots = [eventbrite.cities_due(cities, start + timedelta(hours=6 * i), 6, 28) for i in range(28)]
    assert sorted(c for slot in slots for c in slot) == sorted(cities)
    assert max(len(s) for s in slots) - min(len(s) for s in slots) <= 1
    assert slots[0] == eventbrite.cities_due(cities, start + timedelta(hours=6 * 28), 6, 28)  # the next cycle


def test_soonest_first():
    def row(event_id: str, day: str | None) -> dict:
        return {"id": event_id, "start_date": day, "start_time": "20:00", "timezone": "Asia/Jerusalem"}

    rows = [row("far", "2027-03-01"), row("none", None), row("near", "2026-11-20"), row("mid", "2026-12-05")]
    assert [e["id"] for e in eventbrite.soonest_first(rows)] == ["near", "mid", "far", "none"]


def test_venue_profile_takes_the_organizer_that_is_the_venue():
    assert eventbrite.same_name("LIDO Berlin", "Lido-Berlin") and not eventbrite.same_name("Lido", "Lido Berlin")
    venue = eventbrite._Venue(
        "Lido", "Cuvrystr. 7", 52.5, 13.4, None, "Club in Kreuzberg", "https://www.eventbrite.de/o/lido-1"
    )
    p = eventbrite.venue_profile("eventbrite-7", venue)
    assert (p.key, p.name, p.source, p.about) == ("eventbrite-7", "Lido", "eventbrite", "Club in Kreuzberg")
    assert (p.address, p.latitude, p.longitude) == ("Cuvrystr. 7", 52.5, 13.4)
    assert p.profile_url == "https://www.eventbrite.de/o/lido-1" and p.links == [p.profile_url]


SOON = (datetime.now(UTC) + timedelta(days=10)).date().isoformat()  # within retention.max_future_days


def _row(event_id: str, country: str, tags: list[str], venue: str = "V") -> dict[str, Any]:
    return {
        "id": event_id, "name": f"E{event_id}", "url": f"https://www.eventbrite.com/e/x-{event_id}",
        "start_date": SOON, "start_time": "20:00", "timezone": "Asia/Jerusalem", "tags": [_tag(t) for t in tags],
        "primary_venue": {"id": f"v{venue}", "name": venue, "address": {"city": "C", "country": country}},
    }  # fmt: skip


def _page(rows: list[dict[str, Any]], pages: int = 1) -> str:
    found = {"pagination": {"page_count": pages, "object_count": len(rows)}, "results": rows}
    return f"window.__SERVER_DATA__ = {json.dumps({'search_data': {'events': found}})};"


def test_a_run_reads_israel_and_its_slot_cities():
    music, business, party = "EventbriteCategory/103", "EventbriteCategory/101", "EventbriteFormat/11"
    pages = {  # path -> page -> rows
        "/d/israel/all-events/": {1: [_row("1", "IL", [business])]},
        "/d/israel--haifa/all-events/": {1: [_row("1", "IL", []), _row("2", "IL", [music]), _row("3", "JO", [music])]},
        # A small city: read whole and kept by category (the business event is left out).
        "/d/france--lille/all-events/": {1: [_row("10", "FR", [music]), _row("11", "FR", [business])]},
        # A big city: its listings, not its all-events pages.
        "/d/germany--berlin/all-events/": {1: [_row("20", "DE", [business])]},
        "/d/germany--berlin/music--events/": {1: [_row("21", "DE", [music], "Lido")], 2: [_row("22", "DE", [music])]},
        "/d/germany--berlin/parties/": {1: [_row("21", "DE", [music, party], "Lido")]},
    }
    counts = {"/d/germany--berlin/all-events/": 50, "/d/germany--berlin/music--events/": 2}
    timed_out = {"/d/germany--berlin/parties/"}  # answers on the second try
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/e/"):
            info = {
                "organizer": {"name": "LIDO", "url": "https://www.eventbrite.de/o/lido-1", "description": "<p>Club</p>"}
            }
            context = {"basicInfo": info, "performers": {"headliners": [{"name": "DJ A"}], "supporters": []}}
            body = json.dumps({"props": {"pageProps": {"context": context}}})
            return httpx.Response(200, text=f'<script id="__NEXT_DATA__" type="application/json">{body}</script>')
        asked.append(path)
        if path in timed_out:
            timed_out.discard(path)
            return httpx.Response(200, text=_fixture("search_timed_out.html"))
        if path not in pages:
            return httpx.Response(404, text="")
        page = int(request.url.params.get("page", "1"))
        return httpx.Response(200, text=_page(pages[path].get(page, []), counts.get(path, 1)))

    cfg = eventbrite.EventbriteSettings(
        cron="0 0 * * *", israel_paths=["/d/israel/all-events/", "/d/israel--haifa/all-events/"],
        cities=["france--lille", "germany--berlin", "spain--nowhere"], cycle_runs=1, small_city_pages=8,
        listings={"music--events": "EventbriteCategory/103", "parties": "EventbriteFormat/11"},
        empty_retry_seconds=0,
    )  # fmt: skip
    scraper = eventbrite.EventbriteScraper(
        cfg, HttpSettings(request_delay_seconds=0, backoff_seconds=0), "Asia/Jerusalem"
    )
    scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def run() -> list[ScrapedEvent]:
        return [e async for e in scraper.scrape()]

    events = {e.source_event_id: e for e in asyncio.run(run())}
    assert sorted(events) == ["1", "10", "2", "21", "22"]
    assert "/d/germany--berlin/all-events/" in asked and asked.count("/d/germany--berlin/parties/") == 2
    assert events["21"].artists == ["DJ A"] and events["21"].country == "DE"
    assert events["1"].country is None
    assert (scraper.stats.listed, scraper.stats.events, scraper.stats.empty) == (5, 5, 0)
    lido = next(p for p in scraper.venue_profiles if p.key == "eventbrite-vLido")
    assert (lido.about, lido.profile_url) == ("Club", "https://www.eventbrite.de/o/lido-1")
    assert all(p.about is None for p in scraper.venue_profiles if p.key != "eventbrite-vLido")


LATER = (datetime.now(UTC) + timedelta(days=17)).date().isoformat()


def _api(event_id: str, day: str, venue_id: str = "vV", name: str = "Pub Crawl", **more: Any) -> dict[str, Any]:
    """An API v3 event (a series date, an organizer's event)."""
    return {
        "id": event_id, "url": f"https://www.eventbrite.com/e/x-{event_id}", "name": {"text": name},
        "start": {"local": f"{day}T21:00:00", "timezone": "Asia/Jerusalem"},
        "end": {"local": f"{day}T23:30:00", "timezone": "Asia/Jerusalem"},
        "status": "live", "listed": True, "online_event": False, "venue_id": venue_id, **more,
    }  # fmt: skip


def test_an_api_date_is_the_listed_event_with_its_own_id_times_place_and_picture():
    listed = {**_row("1", "IL", ["EventbriteFormat/11"]), "name": "Pub Crawl", "image_id": "7",
              "image": {"original": {"url": "https://img.evbuc.com/a.jpg"}}, "num_children": 3, "series_id": "S"}  # fmt: skip
    entry = eventbrite.api_entry(_api("2", LATER, logo_id="7", is_series=True, series_id="S"), listed, CFG)
    assert entry is not None
    assert (entry["id"], entry["url"], entry["name"]) == ("2", "https://www.eventbrite.com/e/x-2", "Pub Crawl")
    assert (entry["start_date"], entry["start_time"], entry["end_time"]) == (LATER, "21:00", "23:30")
    assert entry["primary_venue"] == listed["primary_venue"] and entry["tags"] == listed["tags"]
    assert entry["image"] == listed["image"] and entry["num_children"] == 2  # the same picture: the same file
    other = {"name": "Bar 2", "address": {"city": "Tel Aviv", "country": "IL", "latitude": "32.1", "longitude": "34.8"}}
    moved = eventbrite.api_entry(_api("3", LATER, venue_id="v2", venue=other, logo_id="8", logo={"original": {"url": "https://img.evbuc.com/b.jpg"}}), listed, CFG)  # fmt: skip
    assert moved is not None and moved["primary_venue"] == {"id": "v2", **other}
    assert moved["image"] == {"original": {"url": "https://img.evbuc.com/b.jpg"}}
    # A place the API did not describe:
    assert eventbrite.api_entry(_api("4", LATER, venue_id="v2"), listed, CFG) is None
    assert eventbrite.api_entry(_api("5", LATER, status="canceled"), listed, CFG) is None
    assert eventbrite.api_entry(_api("6", LATER, online_event=True), listed, CFG) is None
    assert eventbrite.api_entry(_api("7", LATER, listed=False), listed, CFG) is None
    assert eventbrite.api_entry(_api("8", LATER, is_series_parent=True), listed, CFG) is None
    assert eventbrite.api_entry({**_api("9", LATER), "venue_id": None}, listed, CFG) is None  # no place yet
    assert eventbrite.dedup_count({"dedup": {"count": 4}}) == 4 and eventbrite.dedup_count({}) == 1


def test_every_date_of_an_israeli_series_and_same_name_group_is_read_not_abroad():
    party, music = "EventbriteFormat/11", "EventbriteCategory/103"
    crawl = {**_row("1", "IL", [party]), "name": "Pub Crawl", "series_id": "S1", "num_children": 5}
    sound = {**_row("50", "IL", [music]), "name": "Sound  Night", "dedup": {"count": 2}, "primary_organizer_id": "O1"}
    lille = {**_row("90", "FR", [music]), "series_id": "S9", "num_children": 4}
    pages = {"/d/israel/all-events/": [crawl, sound], "/d/france--lille/all-events/": [lille]}
    bar2 = {"name": "Bar 2", "address": {"city": "Tel Aviv", "country": "IL"}}
    aqaba = {"name": "Club", "address": {"city": "Aqaba", "country": "JO"}}
    api = {  # path -> page -> (events, more pages)
        "/api/v3/series/S1/events/": {
            1: ([_api("1", SOON), _api("2", LATER)], True),
            2: ([_api("3", LATER, venue_id="v2", venue=bar2), _api("4", LATER, status="canceled"),
                 _api("5", LATER, online_event=True)], False),
        },
        "/api/v3/organizers/O1/events/": {
            1: ([_api("50", SOON, name="Sound Night"), _api("51", LATER, name="sound night"),
                 _api("52", LATER, venue_id="vJ", venue=aqaba, name="Sound Night"), _api("53", LATER, name="Other")], False),
        },
    }  # fmt: skip
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        asked.append(path)
        if path.startswith("/e/"):
            context = {"basicInfo": {"status": "live"}, "salesStatus": {"salesStatus": "sold_out"}}
            body = json.dumps({"props": {"pageProps": {"context": context}}})
            return httpx.Response(200, text=f'<script id="__NEXT_DATA__" type="application/json">{body}</script>')
        page = int(request.url.params.get("page", "1"))
        if path in api:
            assert request.url.params.get("expand") == "venue"
            rows, more = api[path][page]
            return httpx.Response(200, json={"events": rows, "pagination": {"has_more_items": more, "page_count": 2}})
        if path not in pages:
            return httpx.Response(404, text="")
        return httpx.Response(200, text=_page(pages[path] if page == 1 else []))

    cfg = eventbrite.EventbriteSettings(
        cron="0 0 * * *", israel_paths=["/d/israel/all-events/"], cities=["france--lille"], cycle_runs=1,
        empty_retry_seconds=0,
    )  # fmt: skip
    scraper = eventbrite.EventbriteScraper(
        cfg, HttpSettings(request_delay_seconds=0, backoff_seconds=0), "Asia/Jerusalem"
    )
    scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def run() -> list[ScrapedEvent]:
        return [e async for e in scraper.scrape()]

    events = {e.source_event_id: e for e in asyncio.run(run())}
    assert sorted(events) == ["1", "2", "3", "50", "51", "90"]
    assert events["2"].start_at is not None and events["2"].start_at.date().isoformat() == LATER
    assert str(events["2"].url) == "https://www.eventbrite.com/e/x-2" == events["2"].extra["purchase_url"]
    assert (events["3"].venue_name, events["3"].city) == ("Bar 2", "Tel Aviv")
    assert events["1"].is_sold_out and not events["2"].is_sold_out  # the page's sale is the listed date's
    assert "/e/x-2" not in asked and "/e/x-51" in asked  # a series date shares its page; a same-name event has its own
    assert asked.count("/api/v3/series/S1/events/") == 2 and "/api/v3/series/S9/events/" not in asked  # not abroad
    assert (scraper.stats.listed, scraper.stats.dates, scraper.stats.events) == (4, 3, 6)
