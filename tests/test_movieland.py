"""Movieland: the all-branches /api/Events feed to one event per movie, branch and business day."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

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
