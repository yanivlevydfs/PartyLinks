"""Hecht Museum (mushecht.haifa.ac.il): one wp-json call to the ``event`` post type (ACF day, series terms).

Fixture (23.11.2026): seven event posts as wp-json sends them with ``_embed`` (only the fields read; the text cut to
2,500 characters): "ארכיאולוגיה עכשיו" lectures (one whose text repeats another date, one with no date line,
an old copy dated 2007), a stand-up evening and a swing night.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.models import ScrapedEvent
from partylinks.scrapers import build_scraper
from partylinks.scrapers.hechtmuseum import HechtMuseumScraper, HechtMuseumSettings, parse_post, ticket_link

TZ = "Asia/Jerusalem"
IL = ZoneInfo(TZ)
FIXTURES = Path(__file__).parent / "fixtures" / "hechtmuseum"
POSTS: list[dict[str, Any]] = json.loads((FIXTURES / "events.json").read_text("utf-8"))
BY_ID = {p["id"]: p for p in POSTS}
CFG = HechtMuseumSettings.model_validate(
    {
        "cron": "0 0 * * *",
        "venue_name": "מוזיאון הכט",
        "address": "שדרות אבא חושי 199, אוניברסיטת חיפה",
        "city": "חיפה",
        "latitude": 32.763218,
        "longitude": 35.018062,
        "labels": {"ארכיאולוגיה עכשיו": ["הרצאה"], "לילות סווינג": ["סווינג"], "רביעי הגדול": ["סטנדאפ"]},
    }
)


def _event(post_id: int) -> ScrapedEvent:
    event = parse_post(BY_ID[post_id], CFG, TZ)
    assert event is not None
    return event


def _genres(event: ScrapedEvent) -> set[str]:
    enriched = Enricher(Settings().enrichment, TZ).enrich(event)
    assert enriched is not None
    return set(enriched.genres)


def test_lecture_day_time_price_and_eventer_link() -> None:
    lecture = _event(3722)
    assert (lecture.source, lecture.source_event_id) == ("hechtmuseum", "3722")
    assert lecture.title == "כפר או אחוזה? חשיפת יישוב שלם מהתקופה הביזנטית מצפון לקריית גת"
    # "תחילת הרצאה 18:00 | התכנסות החל מ-17:30": the first clock is the start.
    assert lecture.start_at == datetime(2027, 6, 13, 18, 0, tzinfo=IL) and lecture.time_known
    assert (lecture.venue_name, lecture.city) == ("מוזיאון הכט", "חיפה")
    assert (lecture.latitude, lecture.longitude) == (32.763218, 35.018062)
    assert lecture.extra["purchase_url"] == "https://www.eventer.co.il/vwq4f"
    assert lecture.min_price == Decimal(35)
    assert lecture.images and lecture.images[0].startswith("https://mushecht.haifa.ac.il/wp-content/uploads/")
    assert lecture.source_genres == ["ארכיאולוגיה עכשיו", "הרצאה"]
    assert "lectures" in _genres(lecture)


def test_the_listing_day_wins_over_a_copied_date_in_the_text() -> None:
    # The post's text still says 07.03.2027 (copied from the next lecture); the listing's day is 7.2.
    assert _event(3672).start_at == datetime(2027, 2, 7, 18, 0, tzinfo=IL)


def test_series_without_a_date_line_still_has_its_clock() -> None:
    assert _event(3661).start_at == datetime(2027, 1, 3, 18, 0, tzinfo=IL)


def test_stand_up_and_swing_nights() -> None:
    standup = _event(3160)
    assert standup.start_at == datetime(2026, 11, 25, 21, 0, tzinfo=IL)  # not the doors' 20:45
    assert "stand_up" in _genres(standup)
    swing = _event(2866)
    assert swing.title == "לילות סווינג – Vivaldi & Co"
    assert swing.start_at == datetime(2026, 11, 26, 19, 30, tzinfo=IL)
    assert "jazz" in _genres(swing)


def test_no_day_no_event_and_social_links_are_no_ticket() -> None:
    post = json.loads(json.dumps(BY_ID[3722]))
    post["acf"]["date"] = ""
    assert parse_post(post, CFG, TZ) is None
    html = '<a href="https://www.facebook.com/hecht">f</a> <a href="https://www.eventer.co.il/abc">כרטיסים</a>'
    assert ticket_link(html, CFG.ticket_hosts) == "https://www.eventer.co.il/abc"
    assert ticket_link('<a href="https://www.facebook.com/hecht">f</a>', CFG.ticket_hosts) is None


def test_run_keeps_current_events_once_nearest_first() -> None:
    copy = json.loads(json.dumps(BY_ID[3722]))
    copy["id"] = 9999  # a newer copy of the same lecture on the same day
    posts = [*POSTS, copy]
    scraper = build_scraper("hechtmuseum", Settings())
    assert isinstance(scraper, HechtMuseumScraper)
    scraper.http.request_delay_seconds = 0

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/wp-json/wp/v2/event"
        return httpx.Response(200, json=posts, headers={"X-WP-Total": str(len(posts))})

    async def run() -> list[ScrapedEvent]:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return [e async for e in scraper.events()]

    events = asyncio.run(run())
    assert not scraper.failures
    ids = [e.source_event_id for e in events]
    assert "3649" not in ids  # the 2007 copy is past
    assert ids.count("9999") == 1 and "3722" not in ids  # one event per title and day: the newer post
    assert [e.start_at for e in events] == sorted(e.start_at for e in events)
