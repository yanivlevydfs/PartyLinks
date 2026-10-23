"""Secret Tel Aviv parser tests against trimmed real pages (the iCal feed, two event pages)."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from partylinks.config import HttpSettings
from partylinks.scrapers import secrettelaviv
from partylinks.scrapers.base import SourceBlockedError

FIXTURES = Path(__file__).parent / "fixtures" / "secrettelaviv"
TZ = "Asia/Jerusalem"
SITE = "https://www.secrettelaviv.com"
CFG = secrettelaviv.SecretTelAvivSettings(cron="52 */3 * * *", site_url=SITE)


def _page(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_feed_lists_every_event() -> None:
    events = {e.source_event_id: e for e in secrettelaviv.parse_feed(_page("feed.ics"), CFG, TZ)}
    assert set(events) == {
        "the-fifth-european-night-of-literature-liebling-haus",
        "alien-transport-frequency-gagarin-club",
        "midburn-festival",
    }
    gagarin = events["alien-transport-frequency-gagarin-club"]
    assert gagarin.title == "Alien Transport & Frequency"  # the venue is its own field, as on the event page
    assert gagarin.venue_name == "Gagarin Club"
    assert gagarin.start_at == datetime(2026, 10, 26, 23, 0, tzinfo=ZoneInfo(TZ))
    assert gagarin.end_at is None  # "11:55 pm" is the site's placeholder end
    assert gagarin.source_genres == [
        "international artists",
        "israeli musicians",
        "parties",
        "rave",
    ]
    assert gagarin.image_url == f"{SITE}/wp-content/uploads/2026/10/image-248.jpg"
    literature = events["the-fifth-european-night-of-literature-liebling-haus"]
    assert literature.end_at == datetime(2026, 10, 21, 22, 0, tzinfo=ZoneInfo(TZ))
    assert literature.description == "Two nights of stories, music, and literature ✨"
    midburn = events["midburn-festival"]
    assert midburn.venue_name is None and midburn.end_at is None  # "All Day", no venue
    assert midburn.start_at == datetime(2026, 11, 2, tzinfo=ZoneInfo(TZ))


def test_blocked_run_still_saves_new_feed_events() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.query == b"ical=1":
            return httpx.Response(200, text=_page("feed.ics"))
        return httpx.Response(403, headers={"cf-mitigated": "challenge"})

    scraper = secrettelaviv.SecretTelAvivScraper(CFG, HttpSettings(request_delay_seconds=0, backoff_seconds=0), TZ)
    scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    scraper.known_images = {"midburn-festival": ["x"]}  # stored: keeps its full details
    got: list[str] = []

    async def run() -> None:
        async for event in scraper.scrape():
            got.append(event.source_event_id)

    with pytest.raises(SourceBlockedError):
        asyncio.run(run())
    assert sorted(got) == [
        "alien-transport-frequency-gagarin-club",
        "the-fifth-european-night-of-literature-liebling-haus",
    ]


def test_event_page_fields() -> None:
    url = f"{SITE}/tickets/alien-transport-frequency-gagarin-club"
    event = secrettelaviv.parse_event(_page("event_gagarin.html"), url, CFG, TZ)
    assert event.source_event_id == "alien-transport-frequency-gagarin-club"
    assert event.title == "Alien Transport & Frequency"
    assert event.start_at == datetime(2026, 10, 26, 23, 0, tzinfo=ZoneInfo(TZ))
    assert event.end_at is None  # "11:55 pm" is the site's placeholder end
    assert event.venue_name == "Gagarin Club" and event.address is None
    assert event.source_genres == [
        "international artists",
        "israeli musicians",
        "parties",
        "rave",
    ]
    assert event.image_url == f"{SITE}/wp-content/uploads/2026/10/image-248.jpg"
    assert event.extra["purchase_url"] == "https://www.instagram.com/p/DeZn7lAKMF0/"
    assert event.description and event.description.startswith("English")
    assert "Crazy Astronaut" in event.description
    assert "Location" not in event.description and "Secret Deals" not in event.description


def test_multi_day_event_runs_to_last_day() -> None:
    url = f"{SITE}/tickets/soundtrack-tel-aviv-film-festival-tel-aviv-cinematheque"
    event = secrettelaviv.parse_event(_page("event_soundtrack.html"), url, CFG, TZ)
    assert event.start_at == datetime(2026, 11, 24, 18, 0, tzinfo=ZoneInfo(TZ))
    assert event.end_at == datetime(2026, 11, 28, 23, 0, tzinfo=ZoneInfo(TZ))
    assert event.venue_name == "Tel Aviv Cinematheque"


def test_end_after_midnight_rolls_to_next_day() -> None:
    start, end = secrettelaviv.parse_when("Fri 30 October 2026 | 11:00 pm - 5:00 am", "11:55 pm", ZoneInfo(TZ))
    assert start == datetime(2026, 10, 30, 23, 0, tzinfo=ZoneInfo(TZ))
    assert end == datetime(2026, 10, 31, 5, 0, tzinfo=ZoneInfo(TZ))
