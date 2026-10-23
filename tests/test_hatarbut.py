"""Heichal HaTarbut: Event Organiser calendar JSON + show page to ScrapedEvent."""

from __future__ import annotations

import json
from pathlib import Path

from partylinks.scrapers.hatarbut import HatarbutSettings, parse_row

TZ = "Asia/Jerusalem"
FIXTURES = Path(__file__).parent / "fixtures/hatarbut"
ROWS = json.loads((FIXTURES / "fullcal.json").read_text("utf-8"))
PAGE = (FIXTURES / "show_page.html").read_text("utf-8")
SETTINGS = HatarbutSettings(
    cron="7 5,17 * * *", site_url="https://www.hatarbut.co.il", category="הופעות-ומוזיקה",
    venue_name="היכל התרבות תל אביב", city="תל אביב-יפו",
)  # fmt: skip


def test_timed_row_with_show_page() -> None:
    e = parse_row(ROWS[0], PAGE, SETTINGS, "hatarbut", TZ)
    assert e.title == "Alessandro Safina — My Way — Live Concert with Orchestra"
    assert e.start_at is not None and e.start_at.isoformat() == "2026-10-25T20:00:00+02:00"
    assert e.end_at is not None and e.end_at.isoformat() == "2026-10-25T23:00:00+02:00"
    assert e.extra == {"purchase_url": "https://il.talisman-show.com/announce/buy/211678", "hall": "לאוי"}
    assert e.images == ["https://www.hatarbut.co.il/wp-content/uploads/2025/03/387-370.jpg"]
    assert e.venue_name == "היכל התרבות תל אביב" and e.city == "תל אביב-יפו"
    assert e.description and e.description.startswith("כוכב האופרה")  # site boilerplate dropped


def test_all_day_row_takes_its_time_from_the_date_list() -> None:
    e = parse_row(ROWS[1], None, SETTINGS, "hatarbut", TZ)
    assert e.start_at is not None and e.start_at.isoformat() == "2026-12-07T17:00:00+02:00"
    assert e.end_at is None and e.images == [] and "purchase_url" not in e.extra


def test_performances_of_one_show_get_distinct_ids() -> None:
    a, b = (parse_row(r, None, SETTINGS, "hatarbut", TZ) for r in ROWS[2:4])
    assert a.url == b.url and a.source_event_id != b.source_event_id
