"""Cluberim (cluberim.co.il): one wp-json call to the ``events`` post type (JetEngine meta + embedded terms).

Fixture (23.11.2026): the nine listed events as wp-json sends them with ``_embed`` (only the fields read; the
text cut to 600 characters): club nights in Tel Aviv and Petah Tikva, a teens' Hanukkah party in Beer Sheva, a
New Year's festival in Emek Hefer and a Purim festival at a secret location.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.models import ScrapedEvent
from partylinks.scrapers import build_scraper
from partylinks.scrapers.cluberim import CluberimScraper, CluberimSettings, parse_post

TZ = "Asia/Jerusalem"
IL = ZoneInfo(TZ)
FIXTURES = Path(__file__).parent / "fixtures" / "cluberim"
POSTS: dict[int, dict[str, Any]] = {p["id"]: p for p in json.loads((FIXTURES / "events.json").read_text("utf-8"))}
CFG = CluberimSettings.model_validate({"cron": "0 0 * * *", "labels": {"סוכות": ["סוכות", "חגים"]}})


def _event(post_id: int) -> ScrapedEvent:
    event = parse_post(POSTS[post_id], CFG, TZ)
    assert event is not None
    return event


def test_club_night_with_its_venue_and_town() -> None:
    night = _event(5156)
    assert (night.source, night.source_event_id) == ("cluberim", "5156")
    assert night.title == "THURSDAY MAINSTREAM | 26.11"
    assert night.start_at == datetime(2026, 11, 26, 22, 0, tzinfo=IL)
    # The area's town, not the address's last part ("נמל תל אביב").
    assert (night.venue_name, night.address, night.city) == ("מון צ׳ילד", "מון צ׳ילד, נמל תל אביב", "תל אביב")
    assert night.extra["purchase_url"].startswith("https://zygo.co.il/event/")
    assert night.images == ["https://cluberim.co.il/wp-content/uploads/2026/10/cluberim-thums26.webp"]
    assert night.source_genres == ["היפ הופ", "מיינסטרים", "חיי לילה ומסיבות"]  # no-break spaces normalised
    enriched = Enricher(Settings().enrichment, TZ).enrich(night)
    assert enriched is not None and {"hip_hop", "mainstream", "nightclubs"} <= set(enriched.genres)


def test_street_address_has_no_venue_and_artists_are_kept() -> None:
    bar = _event(5193)
    assert bar.venue_name is None and (bar.address, bar.city) == ("ברקת 9, פתח תקווה", "פתח תקווה")
    forum = _event(5201)
    assert forum.venue_name == "מועדון הפורום" and forum.city == "באר שבע"
    assert forum.artists == ["Rising Dust"]
    festival = _event(5203)
    assert (festival.address, festival.city) == ("עמק חפר", "עמק חפר")
    assert festival.start_at == datetime(2027, 1, 1, 22, 0, tzinfo=IL)


def test_secret_location_is_not_imported() -> None:
    assert parse_post(POSTS[5197], CFG, TZ) is None  # "ישראל": nowhere to put it


def test_holiday_label() -> None:
    post = json.loads(json.dumps(POSTS[5156]))
    post["_embedded"]["wp:term"].append([{"taxonomy": "event-genre", "name": "סוכות"}])
    event = parse_post(post, CFG, TZ)
    assert event is not None and "חגים" in event.source_genres
    enriched = Enricher(Settings().enrichment, TZ).enrich(event)
    assert enriched is not None and "holidays" in enriched.genres


def test_run_one_call() -> None:
    scraper = build_scraper("cluberim", Settings())
    assert isinstance(scraper, CluberimScraper)
    assert scraper.settings.labels
    scraper.http.request_delay_seconds = 0
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        return httpx.Response(200, json=list(POSTS.values()), headers={"X-WP-Total": str(len(POSTS))})

    async def go() -> list[ScrapedEvent]:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return [e async for e in scraper.events()]

    events = asyncio.run(go())
    assert asked == ["/wp-json/wp/v2/events"]
    assert not scraper.failures
    assert all(e.source_event_id != "5197" for e in events)
    starts = [e.start_at.timestamp() for e in events if e.start_at]
    assert starts == sorted(starts)
