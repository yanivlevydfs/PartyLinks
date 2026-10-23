"""Kikar HaMusica: its Events Calendar feed through the generic Tribe parser."""

from __future__ import annotations

import json
from pathlib import Path

from partylinks.scrapers.kikarhamusica import KikarHamusicaScraper
from partylinks.scrapers.tribe import TribeSettings, parse_event

TZ = "Asia/Jerusalem"
EVENTS = {
    e["id"]: e
    for e in json.loads((Path(__file__).parent / "fixtures/kikarhamusica/events.json").read_text("utf-8"))["events"]
}
SETTINGS = TribeSettings(
    cron="33 3,15 * * *", site_url="https://kikar-hamusica.com/shows/he", venue_name="כיכר המוזיקה",
    address="יואל משה סלומון 10, ירושלים", city="ירושלים",
)  # fmt: skip


def test_show_with_local_times_and_configured_venue() -> None:
    e = parse_event(EVENTS[9892], SETTINGS, KikarHamusicaScraper.name, TZ)
    assert e is not None and e.source == "kikarhamusica" and e.title == "יהודה אשש"
    assert e.start_at is not None and e.start_at.isoformat() == "2026-10-23T09:30:00+03:00"
    assert e.end_at is not None and e.end_at.isoformat() == "2026-10-23T11:30:00+03:00"
    assert (e.venue_name, e.city, e.event_types) == ("כיכר המוזיקה", "ירושלים", ["מוזיקה ישראלית"])
    assert e.images[0].startswith("https://kikar-hamusica.com/shows/he/wp-content/uploads/")
    assert e.min_price is None and not e.tickets and e.extra["purchase_url"] is None  # no seller


def test_every_genre_kept() -> None:
    e = parse_event(EVENTS[9904], SETTINGS, KikarHamusicaScraper.name, TZ)
    assert e is not None and len(e.event_types) == 2 and "R&B" in e.event_types[0]  # entity decoded
