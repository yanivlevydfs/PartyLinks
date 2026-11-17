"""Ironit, Ashdod's culture company (ironit.org.il, 18 November 2026): calendar feed and event pages."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.parsing import ParseError, zone
from partylinks.scrapers.ironit import IronitSettings, next_dates, parse_event

TZ = "Asia/Jerusalem"
FIX = Path(__file__).parent / "fixtures/ironit"
S = IronitSettings(cron="0 0 * * *")
BASE = "https://www.ironit.org.il/events/"
THIRD_EYE = BASE + "%d7%94%d7%a2%d7%99%d7%9f-%d7%94%d7%a9%d7%9c%d7%99%d7%a9%d7%99%d7%aa/"
PARADE = BASE + "%d7%9e%d7%a6%d7%a2%d7%93-%d7%94%d7%90%d7%95%d7%a8-%d7%93%d7%a7%d7%9c/"


def _page(name: str) -> str:
    return (FIX / f"{name}.html").read_text("utf-8")


def test_feed_keeps_each_page_once_at_its_next_date() -> None:
    rows = json.loads((FIX / "feed.json").read_text("utf-8"))
    dates = next_dates(rows, date(2026, 12, 1))
    # The exhibition has a row a day (30.11-03.12): from 1 December it is listed at 1 December.
    assert dates[THIRD_EYE] == date(2026, 12, 1)
    assert all(d >= date(2026, 12, 1) for d in dates.values())
    assert len(dates) < len(rows)
    assert next_dates([{"url": THIRD_EYE, "start": "bad"}, {"title": "x"}], date(2026, 1, 1)) == {}


def test_free_parade_at_its_place_with_its_types() -> None:
    e = parse_event(_page("parade"), PARADE, date(2026, 12, 3), ["מופעים, הרצאות וסדנאות", "מופעים"], S, TZ)
    assert e is not None
    assert e.title == "מצעד האור"
    assert e.start_at == datetime(2026, 12, 3, 17, 30, tzinfo=zone(TZ)) and e.time_known
    assert e.end_at is None
    assert (e.venue_name, e.city) == ('פארק רוטשילד רובע ט"ו', "אשדוד")
    assert e.source_event_id == "22503"
    assert e.source_genres == ["מופעים"]  # the umbrella type is dropped
    assert "purchase_url" not in e.extra
    assert e.images and all(i.startswith("https://www.ironit.org.il/wp-content/uploads/") for i in e.images)
    assert Enricher(Settings().enrichment, TZ).enrich(e).genres


def test_exhibition_run_rolls_to_the_feed_date() -> None:
    e = parse_event(_page("third_eye"), THIRD_EYE, date(2026, 12, 1), ["תערוכות וגלריות"], S, TZ)
    assert e is not None
    assert e.start_at == datetime(2026, 12, 1, 11, 0, tzinfo=zone(TZ))
    assert e.extra["run"] == ["2026-11-30", "2026-12-03"]
    assert e.venue_name == "המשכן לאמנויות הבמה אשדוד"
    assert "exhibitions" in Enricher(Settings().enrichment, TZ).enrich(e).genres


def test_smarticket_sale_is_left_to_the_smarticket_source() -> None:
    page = _page("taverna")
    assert "ashdod.smarticket.co.il" in page
    assert parse_event(page, BASE + "x/", date(2026, 11, 24), ["מופעים"], S, TZ) is None
    other = IronitSettings(cron="0 0 * * *", skip_purchase_hosts=[])
    e = parse_event(page, BASE + "x/", date(2026, 11, 24), ["מופעים"], other, TZ)
    assert e is not None and e.title == "ערב טברנה יוונית - עם אבי טובי"
    assert e.extra["purchase_url"] == "https://ashdod.smarticket.co.il/iframe/event/17758"


def test_courses_are_skipped_and_a_broken_page_raises() -> None:
    assert parse_event(_page("parade"), PARADE, date(2026, 12, 3), ["חוגים וסדנאות"], S, TZ) is None
    with pytest.raises(ParseError):
        parse_event("<html><body></body></html>", PARADE, date(2026, 12, 3), [], S, TZ)
