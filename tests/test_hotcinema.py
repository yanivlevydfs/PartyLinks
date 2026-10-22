"""HOT Cinema: a day's TheaterEvents feed + home-page posters to one event per (movie, branch, day)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from partylinks.models import ScrapedEvent
from partylinks.scrapers.hotcinema import HotCinemaBranch, HotCinemaSettings, parse_day, parse_movies

TZ = "Asia/Jerusalem"
FIXTURES = Path(__file__).parent / "fixtures/hotcinema"
DAY = json.loads((FIXTURES / "theater_events_2026-10-22.json").read_text("utf-8"))
SITE = "https://www.hotcinema.co.il"
SETTINGS = HotCinemaSettings(
    cron="0 6 * * *", site_url=SITE, events_path="/tickets/TheaterEvents",
    branches={16: HotCinemaBranch(name="כפר סבא", address="עתיר ידע 4, מתחם אושילנד", city="כפר סבא",
                                  latitude=32.1654048, longitude=34.9300218)},
)  # fmt: skip


def events() -> dict[str, ScrapedEvent]:
    movies = parse_movies((FIXTURES / "home.html").read_text("utf-8"), SITE)
    return {e.source_event_id: e for e in parse_day(date(2026, 10, 22), DAY, movies, SETTINGS, TZ)}


def test_screenings_grouped_per_movie_branch_day() -> None:
    by_id = events()
    # Toy Story at 3 branches, Clayface at 2 (its 00:00 show stays on the day it was listed), one stand-up.
    assert set(by_id) == {
        "3462:14:2026-10-22", "3462:9:2026-10-22", "3462:17:2026-10-22",
        "3721:16:2026-10-22", "3721:2:2026-10-22", "3849:16:2026-10-22",
    }  # fmt: skip
    clay = by_id["3721:16:2026-10-22"]
    assert clay.title == "קלייפייס" and clay.start_at is not None
    assert clay.start_at.isoformat() == "2026-10-22T17:00:00+03:00"
    assert clay.extra["showtimes"] == [
        "2026-10-22T17:00:00+03:00",
        "2026-10-22T19:25:00+03:00",
        "2026-10-22T21:50:00+03:00",
    ]
    assert clay.description is not None and clay.description.endswith("הקרנות: 17:00, 19:25, 21:50")
    late = by_id["3721:2:2026-10-22"].start_at
    assert late is not None and late.isoformat() == "2026-10-23T00:00:00+03:00"


def test_venue_poster_and_genre() -> None:
    by_id = events()
    clay = by_id["3721:16:2026-10-22"]
    assert (clay.venue_name, clay.city, clay.address) == ("הוט סינמה כפר סבא", "כפר סבא", "עתיר ידע 4, מתחם אושילנד")
    assert clay.latitude == 32.1654048 and clay.source_genres == ["קולנוע"]
    assert clay.image_url == f"{SITE}/images/Clayface-A(1).jpg"
    assert str(clay.url) == f"{SITE}/movie/3721/clayface"
    # Unknown branch: chain name only; a movie with no home-page box links to /movie/<id>.
    assert by_id["3462:9:2026-10-22"].venue_name == "הוט סינמה"
    standup = by_id["3849:16:2026-10-22"]
    assert str(standup.url) == f"{SITE}/movie/3849" and standup.image_url is None
    assert standup.description == "הקרנות: 21:00"
