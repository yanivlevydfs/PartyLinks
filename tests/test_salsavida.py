"""Salsa Vida: the Israel guide's microdata cards, its "load more" windows and each series' page -> weekly nights."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx

from partylinks.config import HttpSettings, Settings
from partylinks.enrichment import Enricher
from partylinks.models import ScrapedEvent
from partylinks.scrapers import build_scraper
from partylinks.scrapers.salsavida import (
    SalsaVidaScraper,
    SalsaVidaSettings,
    parse_cards,
    parse_guide,
    parse_map,
    parse_occurrence,
    parse_series,
    parse_window,
    skipped,
)

TZ = "Asia/Jerusalem"
FIXTURES = Path(__file__).parent / "fixtures/salsavida"
# /guides/israel/ as served on 22.11.2026, cut to four cards (ST Academy, Latino Mind, Havana Club, Media Ram),
# the map items and the "load more" values; the nonce is replaced.
GUIDE = (FIXTURES / "guide.html").read_text("utf-8")
# admin-ajax's answer for the next 14 days, cut to two cards (Rotev Salsa, JLMambo).
WINDOW = json.loads((FIXTURES / "window.json").read_text("utf-8"))
# The Thursday Tel Aviv series' page: its JSON-LD Event and its "About this event" section.
SERIES = (FIXTURES / "series.html").read_text("utf-8")
EVENT = "https://www.salsavida.com/event/israel"
HAVANA = "/event/israel/tel-aviv/havana-club-weekly-salsa-bachata/"
BGU = "/event/israel/be-er-sheva/salsa-and-bachata-tuesdays-at-ben-gurion-university/"


def _settings() -> SalsaVidaSettings:
    return SalsaVidaSettings(cron="7 6,18 * * *", skip_series=[HAVANA, BGU])


def _card(title: str, html: str = GUIDE) -> dict:
    return next(c for c in parse_cards(html) if c["title"] == title)


def test_guide_values_and_map() -> None:
    assert parse_guide(GUIDE) == {
        "nonce": "a1b2c3d4e5", "category": "470", "start": "2026-11-22",
        "event_type": "social_classes", "lang": "en", "window": 14,
    }  # fmt: skip
    places = parse_map(GUIDE)
    assert len(places) == 10
    assert places[EVENT + "/jerusalem/jlmambo-salsa-bachata-in-jerusalem/"] == {
        "cost": "20$", "venue": "JLMambo Studio", "address": "David Ayalon Street 1, Jerusalem 9334329",
        "latitude": 31.7510079, "longitude": 35.1917142,
    }  # fmt: skip
    rotev = places[EVENT + "/tel-aviv/rotev-salsa-weekly-in-kfar-saba/"]  # its lines carry double spaces
    assert (rotev["venue"], rotev["address"]) == ("Rotev Salsa", "3 Sharon, Kfar Saba 44425")
    media = places[EVENT + "/jerusalem/media-ram-cubam-salsa-cuban-salsa-class-party/"]  # no country line
    assert media["address"] == (
        "Edmond and Lily Safra Center for Brain Sciences (ELSC), Hebrew University of Jerusalem, Givat Ram Campus, Jerusalem"
    )


def test_cards() -> None:
    cards = parse_cards(GUIDE)
    assert [c["title"] for c in cards] == [
        "ST Academy Salsa & Bachata Weekly", "Latino Mind Weekly Salsa & Bachata Parties",
        "Havana Club Weekly Salsa & Bachata", "Media Ram Cuban Salsa",
    ]  # fmt: skip
    st = cards[0]
    assert (st["id"], st["start"], st["end"]) == ("23148", "2026-11-22T20:30:00+02:00", "2026-11-22T23:30:00+02:00")
    assert (st["city"], st["organizer"], st["price"]) == ("Peta Tikva", "STstudio", None)
    assert st["families"] == {"classes": ["salsa", "bachata"], "socials": ["salsa", "bachata"], "festivals": []}
    media = cards[3]
    assert (media["price"], media["currency"], media["offer_url"]) == ("0", "ILS", "https://linktr.ee/mngivatram")


def test_series_page() -> None:
    series = parse_series(SERIES)
    assert series["venue"] == "Country Dekel"
    assert (series["street"], series["city"]) == ("69 Rabbi Kozovsky, Tel Aviv, Israel", "Tel Aviv")
    assert (series["latitude"], series["longitude"]) == (32.096741, 34.804012)
    assert series["organizer"] == "Capital Latina"
    text = series["description"] or ""
    assert text.startswith("Capital Latina offers its Salsa and Bachata program") and "About this event" not in text
    assert "Discount cards are available" in text  # the whole text, not the card's cut


def test_night_from_card_and_series() -> None:
    s = _settings()
    card = _card("Latino Mind Weekly Salsa & Bachata Parties")
    series = {
        "venue": "Latino Mind Dance Company - LMDC", "street": "Ha Musakhim St 24, Haifa, Israel", "city": "Haifa",
        "latitude": 32.7935521, "longitude": 35.042579, "organizer": "Latino Mind Dance Company", "description": "Weekly.",
    }  # fmt: skip
    event = parse_occurrence(card, series, parse_map(GUIDE)[card["url"]], s, TZ)
    assert event is not None
    assert event.source_event_id == "23144:2026-11-23"  # the site's own occurrence key
    assert str(event.url) == "https://www.salsavida.com/event/israel/haifa/latino-mind-weekly-salsa-bachata-parties/"
    assert event.start_at is not None and event.start_at.isoformat() == "2026-11-23T21:00:00+02:00"
    assert event.end_at is not None and event.end_at.isoformat() == "2026-11-24T02:00:00+02:00"
    assert (event.venue_name, event.address, event.city) == (
        "Latino Mind Dance Company - LMDC",
        "Ha Musakhim St 24, Haifa",
        "Haifa",
    )
    assert (event.latitude, event.longitude) == (32.7935521, 35.042579)
    assert event.source_genres == ["salsa", "bachata"]
    assert (event.min_price, event.max_price) == (50, 50)
    assert event.extra == {"purchase_url": "https://en.latino-mind.com/"}
    assert event.images == []  # the site's generic dancers picture is no artwork
    enriched = Enricher(Settings().enrichment, TZ).enrich(event)
    assert enriched is not None and {"latin_dance", "salsa"} <= set(enriched.genres)
    assert enriched.city == "חיפה"


def test_map_places_the_night_when_the_series_page_failed() -> None:
    card = _card("ST Academy Salsa & Bachata Weekly")
    event = parse_occurrence(card, {}, parse_map(GUIDE)[card["url"]], _settings(), TZ)
    assert event is not None and event.min_price is None  # "Varies"
    assert (event.venue_name, event.address) == ("ST Academy", "6 HaMefalsim Street, Kiryat Aryeh, Petah Tikva")
    assert (event.latitude, event.longitude) == (32.084041, 34.887762)
    assert event.organizer == "STstudio"
    assert event.description and event.description.startswith("ST Academy offers")  # the card's own text
    enriched = Enricher(Settings().enrichment, TZ).enrich(event)
    assert enriched is not None and enriched.city == "פתח תקווה"  # the card's "Peta Tikva", by its address
    # No map item either: the card's address alone.
    bare = parse_occurrence(card, {}, {}, _settings(), TZ)
    assert bare is not None and bare.venue_name is None and bare.latitude is None
    assert bare.address == "ST Academy 6 HaMefalsim Street, Kiryat Aryeh Petah Tikva Israel"


def test_free_night_and_its_own_artwork() -> None:
    card = _card("Media Ram Cuban Salsa")
    event = parse_occurrence(card, {}, {"cost": "Free"}, _settings(), TZ)
    assert event is not None and (event.min_price, event.max_price) == (0, 0)
    # The organizer's own logo, at full size (the card shows a 240x240 copy).
    assert event.images == [
        "https://www.salsavida.com/wp-content/uploads/sv-ai-event-intake/logo_10__100dpi-goqKhdzj6Z.png"
    ]
    assert event.source_genres == ["salsa"]


def test_price_in_another_currency_is_dropped() -> None:
    cards = parse_cards(parse_window(WINDOW)["html"])
    jlmambo = next(c for c in cards if c["title"].startswith("JLMambo"))
    assert jlmambo["price"] == "20.00"  # the card says ILS, the site's own label says "20$"
    event = parse_occurrence(jlmambo, {}, {"cost": "20$"}, _settings(), TZ)
    assert event is not None and event.min_price is None
    rotev = parse_occurrence(
        next(c for c in cards if c["title"].startswith("Rotev")), {}, {"cost": "NIS 40"}, _settings(), TZ
    )
    assert rotev is not None and rotev.min_price == 40


def test_class_only_and_broken_cards_are_skipped() -> None:
    s = _settings()
    card = _card("Latino Mind Weekly Salsa & Bachata Parties")
    assert (
        parse_occurrence({**card, "families": {"classes": ["salsa"], "socials": [], "festivals": []}}, {}, {}, s, TZ)
        is None
    )
    assert parse_occurrence({**card, "id": ""}, {}, {}, s, TZ) is None
    assert parse_occurrence({**card, "start": None}, {}, {}, s, TZ) is None
    assert parse_occurrence({**card, "title": None}, {}, {}, s, TZ) is None
    # A festival with no social style is still read.
    festival = {**card, "families": {"classes": [], "socials": [], "festivals": ["salsa"]}}
    assert parse_occurrence(festival, {}, {}, s, TZ) is not None
    # An end before the start (a placeholder) is dropped.
    event = parse_occurrence({**card, "end": "2026-11-23T20:00:00+02:00"}, {}, {}, s, TZ)
    assert event is not None and event.end_at is None


def test_skip_series_and_window_answers() -> None:
    s = _settings()
    assert skipped("https://www.salsavida.com" + HAVANA, s.skip_series)
    assert skipped("https://www.salsavida.com" + HAVANA.rstrip("/"), s.skip_series)
    assert not skipped(
        "https://www.salsavida.com/event/israel/haifa/latino-mind-weekly-salsa-bachata-parties/", s.skip_series
    )
    assert parse_window({"success": False, "data": "bad nonce"}) == {}
    assert parse_window({"success": True, "data": "<article></article>"}) == {"html": "<article></article>"}
    assert parse_window(WINDOW)["next_offset"] == 28


def _run(
    settings: SalsaVidaSettings, horizon_days: int
) -> tuple[list[ScrapedEvent], list[str], list[dict], SalsaVidaScraper]:
    scraper = SalsaVidaScraper(settings.model_copy(update={"horizon_days": horizon_days}), HttpSettings(), TZ)
    scraper.http.request_delay_seconds = 0
    asked: list[str] = []
    posted: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(f"{request.method} {request.url.path}")
        if request.method == "POST":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            posted.append(form)
            offset = int(form["offset"])
            # The fixture's two cards for days +14..+27, then an empty window (the site's would hold later nights).
            html = WINDOW["data"]["html"] if offset == 14 else ""
            answer = {"success": True, "data": {"html": html, "next_offset": offset + 14, "has_more": True}}
            return httpx.Response(200, json=answer)
        page = GUIDE if request.url.path == "/guides/israel/" else SERIES
        return httpx.Response(200, text=page, headers={"content-type": "text/html; charset=utf-8"})

    async def go() -> list[ScrapedEvent]:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return [e async for e in scraper.scrape()]

    return asyncio.run(go()), asked, posted, scraper


def test_run_pages_to_the_horizon_and_yields_nearest_first() -> None:
    events, asked, posted, scraper = _run(_settings(), 42)
    assert not scraper.failures
    assert asked[0] == "GET /guides/israel/"
    # Windows at +14 and +28 days, then the 42-day horizon; each asks as the page's button does.
    assert [p["offset"] for p in posted] == ["14", "28"]
    assert posted[0] == {
        "action": "load_more_events", "nonce": "a1b2c3d4e5", "mode": "append", "start_ymd": "2026-11-22",
        "offset": "14", "current_cat": "470", "event_type": "social_classes", "lang": "en",
    }  # fmt: skip
    series_pages = [a for a in asked if a.startswith("GET /event/")]
    assert len(series_pages) == len(set(series_pages)) == 5  # once each: ST, Latino Mind, Media Ram, Rotev, JLMambo
    assert not any(HAVANA in a for a in asked)  # skipped series: not even its page
    assert all("Havana" not in e.title for e in events)
    starts = [e.start_at.timestamp() for e in events if e.start_at]
    assert starts == sorted(starts)  # nearest first; past nights (once the fixture's dates pass) are left out
    assert len(events) <= 5 and len({e.source_event_id for e in events}) == len(events)


def test_a_window_that_does_not_move_on_stops_the_run() -> None:
    scraper = SalsaVidaScraper(_settings(), HttpSettings(), TZ)
    scraper.http.request_delay_seconds = 0
    posts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            posts.append(1)
            return httpx.Response(200, json=WINDOW)  # always "next_offset": 28
        page = GUIDE if request.url.path == "/guides/israel/" else SERIES
        return httpx.Response(200, text=page, headers={"content-type": "text/html; charset=utf-8"})

    async def go() -> list[ScrapedEvent]:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return [e async for e in scraper.scrape()]

    events = asyncio.run(go())
    assert len(posts) == 2  # +14 -> 28, +28 -> 28 again: stop
    assert len({e.source_event_id for e in events}) == len(events)  # the repeated window's nights once


def test_config_block() -> None:
    """Runs once the lead adds sources.salsavida to config/settings.yaml and SalsaVidaScraper to SCRAPERS."""
    scraper = build_scraper("salsavida", Settings())
    assert isinstance(scraper, SalsaVidaScraper)
    assert HAVANA in scraper.settings.skip_series and BGU in scraper.settings.skip_series
    # Its styles are precise and its text names places: "Hebrew Youth House" read as house music.
    settings = Settings()
    assert "salsavida" in settings.enrichment.declared_genres_only
    card = _card("Latino Mind Weekly Salsa & Bachata Parties")
    event = parse_occurrence(card, {"description": "At the Hebrew Youth House."}, {}, scraper.settings, TZ)
    enriched = Enricher(settings.enrichment, TZ).enrich(event) if event else None
    assert enriched is not None and "house" not in enriched.genres and "salsa" in enriched.genres
