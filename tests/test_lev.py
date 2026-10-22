"""Lev cinemas: a branch-day showtimes list plus a movie page to one event per movie."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from partylinks.scrapers.lev import LevBranch, build_events, parse_movie, parse_showtimes

TZ = "Asia/Jerusalem"
FIXTURES = Path(__file__).parent / "fixtures/lev"
SHOWTIMES = parse_showtimes((FIXTURES / "showtimes.html").read_text("utf-8"))
MOVIE = parse_movie((FIXTURES / "movie.html").read_text("utf-8"))
TLV = LevBranch(name="לב תל אביב", venue_name="לב דיזנגוף", address="דיזנגוף 50, דיזנגוף סנטר", city="תל אביב")
OUR_LOVES = "https://www.lev.co.il/movies/%d7%94%d7%90%d7%94%d7%91%d7%95%d7%aa-%d7%a9%d7%9c%d7%a0%d7%95/"


def test_screenings_grouped_per_movie_and_day() -> None:
    assert len(SHOWTIMES) == 14  # the commented-out twins of each <li> are not screenings
    events = {e.source_event_id: e for e in build_events(SHOWTIMES, "telaviv", TLV, date(2026, 10, 22), {}, TZ)}
    assert set(events) == {
        "האהבות-שלנו:telaviv:2026-10-22",
        "כבשה-בקופסה:telaviv:2026-10-22",
        "עצמאות:telaviv:2026-10-22",
    }
    loves = events["האהבות-שלנו:telaviv:2026-10-22"]
    assert loves.start_at is not None and loves.start_at.isoformat() == "2026-10-22T11:00:00+03:00"
    assert loves.extra["showtimes"][-1] == "2026-10-22T21:15:00+03:00"
    assert loves.description == "הקרנות: 11:00, 13:30, 16:00, 18:30, 21:15"
    assert (loves.venue_name, loves.city, loves.source_genres) == ("לב דיזנגוף", "תל אביב", ["קולנוע"])
    assert loves.extra["purchase_url"] == "https://www.lev.co.il/order/?pcode=608691&loc=5"
    # Another day of the same branch is another event.
    other = build_events(SHOWTIMES, "telaviv", TLV, date(2026, 10, 23), {}, TZ)
    assert {e.source_event_id for e in other}.isdisjoint(events)


def test_movie_page_adds_poster_synopsis_and_rating() -> None:
    assert MOVIE["movie_code"] == "4999" and MOVIE["runtime_minutes"] == 117
    assert (MOVIE["country"], MOVIE["year"], MOVIE["language"]) == ("ישראל", 2026, "עברית")
    loves = next(e for e in build_events(SHOWTIMES, "telaviv", TLV, date(2026, 10, 22), {OUR_LOVES: MOVIE}, TZ)
                 if e.extra["movie_id"] == "האהבות-שלנו")  # fmt: skip
    assert loves.title == "האהבות שלנו" and loves.min_age == 12
    assert loves.image_url == "https://www.lev.co.il/wp-content/uploads/2026/10/4999.jpg"
    assert loves.description is not None and loves.description.startswith('ה"האהבות שלנו" – סרטו החדש של אבי נשר')
    assert loves.description.endswith("\n\nהקרנות: 11:00, 13:30, 16:00, 18:30, 21:15")
    assert loves.extra["directors"] == "אבי נשר" and loves.source_genres == ["קולנוע"]
