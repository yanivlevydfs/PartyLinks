"""Tiuli parser test against a trimmed real /events page (4 cards) and one event page."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from partylinks.scrapers import tiuli

FIXTURES = Path(__file__).parent / "fixtures" / "tiuli"
TZ = "Asia/Jerusalem"
TODAY = date(2026, 10, 23)


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_tiuli_listing_keeps_dated_cards() -> None:
    cards = tiuli.parse_listing(_read("listing.html"))

    # The undated cards (a "special" hub, an article with no dates) are not events.
    assert [c.page_id for c in cards] == ["events-13880", "events-15087", "articles-3124"]
    rishon = cards[1]
    assert rishon.title.startswith("חוגגות את מה שיש") and rishon.category == "מיוחד"
    assert rishon.places == ["ראשון לציון"]
    assert cards[2].places == ["באר אורה", "דרום הנגב והערבה הדרומית", "דרום הנגב והרי אילת"]


def test_tiuli_card_dates() -> None:
    assert tiuli.card_dates("29/10 , 30/10", TODAY) == ([date(2026, 10, 29), date(2026, 10, 30)], None)
    # A run over new year; a date long gone is next year's.
    assert tiuli.card_dates("17/11 עד 31/01", TODAY) == ([date(2026, 11, 17)], date(2027, 1, 31))
    assert tiuli.card_dates("01/07 עד 28/11", TODAY) == ([date(2026, 7, 1)], date(2026, 11, 28))
    assert tiuli.card_dates("08/01", TODAY) == ([date(2027, 1, 8)], None)


def test_tiuli_event_with_page() -> None:
    card = tiuli.parse_listing(_read("listing.html"))[1]
    (event,) = tiuli.build_events(card, _read("event.html"), TODAY, TZ)

    assert event.source_event_id == "events-15087" and event.city == "ראשון לציון"
    assert event.start_at == datetime(2026, 10, 29, 19, 30, tzinfo=ZoneInfo(TZ))
    assert event.address == "רחוב דוד רמז 95, ראשון לציון (חניה חינם במקום)."
    assert event.extra["purchase_url"].startswith("https://she-na.org/")
    assert event.image_url == "https://www.tiuli.com/image/7e874317280d5e1f7de52cf3c84e1320.jpeg"


def test_tiuli_card_only_events() -> None:
    run, _, beer_ora = tiuli.parse_listing(_read("listing.html"))

    # A run that has not started yet keeps its first day; no page = no link, place chain as address.
    (exhibit,) = tiuli.build_events(run, None, TODAY, TZ)
    assert exhibit.start_at == datetime(2026, 11, 17, tzinfo=ZoneInfo(TZ))
    assert exhibit.end_at == datetime(2027, 1, 31, 23, 59, tzinfo=ZoneInfo(TZ))
    assert exhibit.extra["purchase_url"] is None and exhibit.address == "ירושלים"
    # Once started it is re-dated to today, so it stays upcoming until its last day.
    (later,) = tiuli.build_events(run, None, date(2026, 12, 5), TZ)
    assert later.start_at == datetime(2026, 12, 5, tzinfo=ZoneInfo(TZ))

    # Several dates: one event each, time unknown (midnight).
    first, second = tiuli.build_events(beer_ora, None, TODAY, TZ)
    assert first.source_event_id == "articles-3124:20261029"
    assert second.start_at == datetime(2026, 10, 30, tzinfo=ZoneInfo(TZ))
    assert first.extra["area"] == "דרום הנגב והערבה הדרומית | דרום הנגב והרי אילת"
