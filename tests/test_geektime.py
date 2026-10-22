"""Geektime event cards and People & Computers' RSS feed to ScrapedEvent."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from partylinks.scrapers.geektime import event_day, parse_page
from partylinks.scrapers.peoplecomputers import parse_feed

TZ = "Asia/Jerusalem"
FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 10, 21)


def test_geektime_cards_skip_online_and_spam() -> None:
    page = (FIXTURES / "geektime/event.html").read_text("utf-8")
    events = parse_page(page, "https://www.geektime.co.il/event/", TZ, TODAY, ["gold bar"])
    assert [e.source_event_id for e in events] == ["593649", "594130"]  # 593658 online, 586262 spam
    mongo, seed = events
    # "10:00 - 00:45" ends after midnight.
    assert mongo.start_at is not None and mongo.end_at is not None
    assert mongo.start_at.isoformat() == "2026-11-02T10:00:00+02:00"
    assert mongo.end_at.isoformat() == "2026-11-03T00:45:00+02:00"
    assert seed.title == "Cracking the Seed Round" and seed.url.unicode_string() == "https://luma.com/b3br07wf"
    assert seed.address and seed.address.startswith("תל אביב מגדלי עזריאלי")
    assert seed.event_types == ["סטארטאפ ויזמות"] and seed.source_genres == ["hi-tech"]
    assert seed.description and seed.description.startswith("Cracking the Seed Round is") and seed.images


def test_geektime_year_from_weekday() -> None:
    assert event_day(9, 6, 2, TODAY) == date(2027, 6, 9)  # Wednesday 9 June is 2027
    assert event_day(21, 10, 2, TODAY) == date(2026, 10, 21)
    assert event_day(5, 1, None, TODAY) == date(2027, 1, 5)


def test_people_and_computers_feed() -> None:
    events = parse_feed((FIXTURES / "peoplecomputers/feed.rss").read_text("utf-8"))
    assert len(events) == 8
    mamram = events[1]
    assert mamram.title == 'הכנס השנתי ממר"ם 2026' and mamram.source_event_id == "i-love-mamram-2026.events.co.il/home"
    assert mamram.start_at is not None
    assert mamram.start_at.isoformat() == "2026-11-12T09:00:00+02:00"
    assert mamram.venue_name == "אקספו תל אביב" and mamram.address == "שדרות רוקח 101, תל אביב"
    assert mamram.images and mamram.description and "יזמות" in mamram.description
    assert mamram.source_genres == ["hi-tech", "conference"]
