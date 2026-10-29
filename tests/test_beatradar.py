"""Beat Radar parser tests against the real /api/events answer (October 2026, first 12 parties)."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.scrapers import beatradar

ITEMS = json.loads((Path(__file__).parent / "fixtures" / "beatradar" / "events.json").read_text(encoding="utf-8"))
TZ = "Asia/Jerusalem"


def test_party_keeps_the_ticket_link_prices_and_genres() -> None:
    event = beatradar.parse_event(ITEMS[2], TZ)
    assert event is not None
    assert str(event.url) == "https://www.go-out.co/event/1792230130132"  # Beat Radar's ref tag dropped
    assert event.venue_name == "Moonchild - מון צ'ילד" and event.city == "Tel Aviv-Jaffa"
    assert (event.min_price, event.max_price) == (Decimal("108.00"), Decimal("110.40"))  # fee float rounded
    assert event.source_genres.count("EDM") == 1 and event.event_types == ["dance party", "dj set"]
    assert event.min_age == 18 and event.artists


def test_region_is_no_city_and_zero_prices_are_unknown() -> None:
    retro = beatradar.parse_event(ITEMS[1], TZ)
    festival = beatradar.parse_event(ITEMS[0], TZ)
    assert retro is not None and festival is not None
    assert "music festival" in retro.event_types and "live show" in retro.event_types
    assert all(t.price is None or t.price > 0 for t in festival.tickets)
    assert festival.city is None  # "Israel"


def test_every_party_parses_and_maps_to_our_genres() -> None:
    settings = Settings()
    enricher = Enricher(settings.enrichment, settings.timezone)
    events = [e for e in (beatradar.parse_event(i, TZ) for i in ITEMS) if e is not None]
    assert len(events) == len(ITEMS)
    assert all(enricher._detect_genres(e) for e in events)


def test_new_genres_and_tech_house_is_not_hi_tech() -> None:
    settings = Settings()
    enricher = Enricher(settings.enrichment, settings.timezone)
    event = beatradar.parse_event(ITEMS[2], TZ)
    assert event is not None
    found = enricher._detect_genres(
        event.model_copy(update={"source_genres": ["tech house", "dark wave", "yoga"], "event_types": []})
    )
    assert found == ["dark_wave", "house", "yoga_wellness"]


def test_cloudflare_403_is_a_blocked_run() -> None:
    import httpx

    scraper = beatradar.BeatRadarScraper.__new__(beatradar.BeatRadarScraper)  # is_blocked reads only the response
    page = httpx.Response(403, headers={"server": "cloudflare"}, text="<!DOCTYPE html>")
    assert scraper.is_blocked(page)
    assert not scraper.is_blocked(httpx.Response(403, headers={"server": "nginx"}))
    assert not scraper.is_blocked(httpx.Response(200, headers={"server": "cloudflare"}, json=[]))
