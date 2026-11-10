"""Movieland: the all-branches /api/Events feed to one event per movie, branch and business day."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from partylinks.scrapers.movieland import MovielandBranch, MovielandSettings, parse_events

TZ = "Asia/Jerusalem"
MOVIES = json.loads((Path(__file__).parent / "fixtures/movieland/events.json").read_text("utf-8"))
SETTINGS = MovielandSettings(
    cron="2 8,20 * * *",
    site_url="https://movieland.co.il",
    branches={"1291": MovielandBranch(venue_name="מובילנד חיפה", address="משה פלימן 4, קניון עזריאלי", city="חיפה")},
)
DAYS = {date(2026, 10, 22)}


def test_grouped_per_movie_branch_and_business_day() -> None:
    events = {e.source_event_id: e for e in parse_events(MOVIES, SETTINGS, DAYS, TZ)}
    assert set(events) == {
        "4904:1291:2026-10-22", "4904:1293:2026-10-22", "5063:1291:2026-10-22", "5063:1293:2026-10-22",
        "5050:1291:2026-10-22", "5050:1293:2026-10-22",
    }  # fmt: skip
    clay = events["4904:1291:2026-10-22"]
    # The 00:25 screening of the 23rd belongs to the 22nd, listed last.
    assert clay.extra["showtimes"][-1] == "2026-10-23T00:25:00+03:00"
    assert clay.description is not None and clay.description.endswith(", 00:25")
    assert clay.start_at is not None and clay.start_at.date() == date(2026, 10, 22)
    assert (clay.venue_name, clay.city, clay.min_age) == ("מובילנד חיפה", "חיפה", 14)
    assert str(clay.url) == "https://movieland.co.il/movie/4904"
    assert clay.image_url and clay.image_url.startswith("https://movieland.co.il/images/")
    # A branch missing from settings is named after the feed.
    assert events["5050:1293:2026-10-22"].venue_name == 'מובילנד הצוק ת"א'


def test_non_film_items_are_not_cinema() -> None:
    days = {date(2026, 10, 22) + timedelta(days=i) for i in range(11)}
    events = {e.extra["movie_id"]: e for e in parse_events(MOVIES, SETTINGS, days, TZ)}
    assert events[5095].source_genres == [] and events[5095].min_age is None  # football broadcast
    assert events[5050].source_genres == ["קולנוע"] and events[5050].min_age == 12


def test_started_screenings_are_left_out() -> None:
    """An evening run starts each day at its next screening: a 14:00 start would read as over by 20:00."""
    now = datetime(2026, 10, 22, 20, 0, tzinfo=ZoneInfo(TZ))
    events = parse_events(MOVIES, SETTINGS, DAYS, TZ, now)
    assert events and all(e.start_at is not None and e.start_at >= now for e in events)
    assert all(s >= now.isoformat() for e in events for s in e.extra["showtimes"])
    assert events[0].extra["purchase_url"].startswith("https://")


def test_scrape_prices_each_event_from_the_shop(monkeypatch) -> None:
    """One login per branch site, one price request per event and screening kind; a failing one keeps the event."""
    import asyncio
    from decimal import Decimal

    from partylinks.config import HttpSettings
    from partylinks.scrapers import movieland

    shop = json.loads((Path(__file__).parent / "fixtures/biggerpicture/event_min_1290-116268.json").read_text("utf-8"))
    scraper = movieland.MovielandScraper(SETTINGS, HttpSettings(), TZ)
    calls: list[str] = []

    async def get_json(url: str, **kwargs: object) -> object:
        calls.append(url)
        if url.endswith("/api/Events"):
            return MOVIES
        if url.endswith("/1293-24605/min"):  # one screening's price fails
            raise RuntimeError("boom")
        return shop

    async def post_json(url: str, *, json: dict[str, object], **kwargs: object) -> object:
        calls.append(f"login {json['siteId']}")
        return {"token": "t"}

    scraper.get_json = get_json  # type: ignore[method-assign]
    scraper.post_json = post_json  # type: ignore[method-assign]
    morning = datetime(2026, 10, 22, 8, tzinfo=ZoneInfo(TZ))
    monkeypatch.setattr(movieland, "datetime", type("D", (datetime,), {"now": staticmethod(lambda tz=None: morning)}))

    async def collect() -> list[object]:
        async with scraper:
            return [e async for e in scraper.scrape()]

    events = {e.source_event_id: e for e in asyncio.run(collect())}
    assert not events.pop("4904:1293:2026-10-22").tickets and len(scraper.failures) == 1
    priced = list(events.values())
    assert priced and all(e.min_price == 50 and e.tickets[0].total_price == Decimal("54.5") for e in priced)
    assert sorted(c for c in calls if c.startswith("login")) == ["login 1291", "login 1293"]
    assert len([c for c in calls if c.endswith("/min")]) == len(events) + 1 + sum(
        len({(s["vip"], s["three_d"]) for s in e.extra["screenings"]}) - 1 for e in events.values()
    )
