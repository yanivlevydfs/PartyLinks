"""viagogo: explore-feed rows on a trimmed real payload (no network)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from partylinks.scrapers.viagogo import ViagogoSettings, infer_date, parse_event

FIXTURE = Path(__file__).parent / "fixtures" / "viagogo" / "explore_page.json"
SETTINGS = ViagogoSettings(
    cron="41 6,18 * * *",
    site_url="https://www.viagogo.com",
    explore_path="/explore",
    locale_path="/il",
    latitude=31.4,
    longitude=34.9,
    radius_m=300000,
)
TODAY = date(2026, 10, 23)


def parse_all() -> list:
    rows = json.loads(FIXTURE.read_text(encoding="utf-8"))["events"]
    return [parse_event(r, SETTINGS, "Asia/Jerusalem", TODAY) for r in rows]


def test_parse_concert() -> None:
    event = parse_all()[0]
    assert event.source_event_id == "162375498"
    assert event.title.startswith("NEXT - Omer Adam")
    assert str(event.url) == "https://www.viagogo.com/il/Concert-Tickets/Other-Concerts/Omer-Adam-Tickets/E-162375498"
    assert event.start_at is not None and event.start_at.isoformat() == "2026-10-24T20:50:00+03:00"
    assert (event.venue_name, event.city, event.country) == ("Ramat Gan Stadium", "Tel Aviv", None)
    assert event.event_types == ["Concert"] and event.source_genres == ["Other Concerts"]
    assert event.image_url and "categories/54350" in event.image_url
    assert event.extra["time_confirmed"] is True


def test_sports_and_abroad_are_skipped() -> None:
    soccer, abroad, jerusalem_soccer, galkin = parse_all()[1:]
    assert soccer is None and jerusalem_soccer is None  # Sports-Tickets: the site is parties, shows and culture
    assert abroad is None  # Wadi Musa, Jordan: inside the circle, outside Israel
    assert galkin.city == "Petah Tikva"


def test_year_from_weekday() -> None:
    galkin = parse_all()[-1]
    assert galkin.start_at is not None and galkin.start_at.isoformat() == "2027-05-09T20:00:00+03:00"
    assert infer_date("Oct 22", "Thu", TODAY) == date(2026, 10, 22)  # yesterday still counts
    assert infer_date("Oct 21", None, TODAY) == date(2027, 10, 21)
    assert infer_date("Nov 05", "Fri", TODAY) == date(2027, 11, 5)  # weekday picks the later year
    assert infer_date("Smarch 3", "Mon", TODAY) is None
