"""Haifa municipality: city-events cards and event pages to ScrapedEvent."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from partylinks.scrapers.haifamuni import parse_event, parse_listing

TZ = "Asia/Jerusalem"
FIXTURES = Path(__file__).parent / "fixtures/haifamuni"
URL = "https://www.haifa.muni.il/event/x/"


def _event(n: int) -> list:
    return parse_event((FIXTURES / f"event_{n}.html").read_text("utf-8"), "1", URL, "חיפה", TZ)


def test_listing_each_post_once() -> None:
    cards = parse_listing((FIXTURES / "city_events.html").read_text("utf-8"))
    assert [post_id for post_id, _ in cards] == ["756794", "651731"]  # 756794 repeats in another rail
    assert cards[0][1].startswith("https://www.haifa.muni.il/event/")


def test_paid_event_with_seller_link() -> None:
    [e] = _event(10)
    assert e.source_event_id == "1:202610242100" and e.start_at.isoformat() == "2026-10-24T21:00:00+03:00"
    assert (e.venue_name, e.address, e.city) == ("בית אבא חושי", "אבא הלל סילבר 71", "חיפה")
    assert (e.min_price, e.max_price) == (Decimal(169), Decimal(189)) and len(e.tickets) == 2
    assert e.extra["purchase_url"].startswith("https://shamayim.smarticket.co.il/")
    assert e.source_genres == ["בית אבא חושי", "הופעות מוזיקה"] and e.image_url and e.description


def test_one_event_per_performance() -> None:
    events = _event(25)
    assert [e.start_at.isoformat() for e in events] == ["2026-10-25T20:30:00+02:00", "2026-10-26T20:30:00+02:00"]
    assert len({e.source_event_id for e in events}) == 2 and events[0].tickets == []
    assert events[0].extra["purchase_url"] == "https://www.ht1.co.il/Event/Index/43815"


def test_free_season_kept_as_first_day() -> None:
    [e] = _event(55)
    assert e.start_at.isoformat() == "2026-01-07T00:00:00+02:00"
    assert e.min_price == 0 and e.tickets[0].is_free and e.extra == {}


def test_undated_page_gives_nothing() -> None:
    page = '<div id="top_content"><h1>סטנדאפ</h1><div class="dates-wrapper"> </div></div>'
    assert parse_event(page, "1", URL, "חיפה", TZ) == []
