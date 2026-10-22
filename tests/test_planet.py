"""Planet / Rav-Hen: a branch's day of film-events to one ScrapedEvent per film."""

from __future__ import annotations

import json
from pathlib import Path

from partylinks.scrapers.planet import parse_film_events, split_attributes

TZ = "Asia/Jerusalem"
FIXTURES = Path(__file__).parent / "fixtures/planet"
BRANCH = json.loads((FIXTURES / "cinemas.json").read_text("utf-8"))["body"]["cinemas"][0]
DAY = json.loads((FIXTURES / "film_events_1025_2026-10-22.json").read_text("utf-8"))["body"]


def test_screenings_grouped_per_film_and_business_day() -> None:
    events = {e.source_event_id: e for e in parse_film_events(DAY, BRANCH, "planet", TZ)}
    assert set(events) == {"8328s2r:1025:2026-10-22", "7141s2r:1025:2026-10-22", "8590s2r:1025:2026-10-22"}
    loves = events["8590s2r:1025:2026-10-22"]
    assert loves.title == "האהבות שלנו" and loves.start_at is not None
    assert loves.start_at.isoformat() == "2026-10-22T14:30:00+03:00"
    # The 00:00 screening belongs to the 22nd's business day, listed last.
    assert loves.extra["showtimes"][-1] == "2026-10-23T00:00:00+03:00"
    assert loves.description == "הקרנות: 14:30, 17:00, 19:30, 22:05, 00:00"
    assert loves.min_age == 12 and loves.extra["movie_genres"] == ["drama", "israeli"]
    assert str(loves.url) == "https://www.planetcinema.co.il/films/our-loves/8590s2r"


def test_venue_and_genres() -> None:
    event = parse_film_events(DAY, BRANCH, "planet", TZ)[0]
    assert (event.venue_name, event.address, event.city) == ("פלאנט אילון", "קניון אילון - אבא הלל סילבר 301", "רמת גן")
    assert (event.latitude, event.longitude) == (32.100334, 34.826633)
    assert all(e.source_genres == ["קולנוע"] for e in parse_film_events(DAY, BRANCH, "planet", TZ))
    assert event.image_url and event.image_url.endswith(".jpg")


def test_attributes() -> None:
    assert split_attributes(["all", "2d", "drama", "first-subbed-lang-he", "subbed"]) == (
        None, ["drama"], ["2d"], ["first-subbed-lang-he", "subbed"],
    )  # fmt: skip
    assert split_attributes(["16-plus", "imax", "dubbed-lang-he"])[0] == 16
