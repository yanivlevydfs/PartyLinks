"""Under The Radar: its calendar feed, home page card and launch data, trimmed real answers (28.11.2026)."""

from __future__ import annotations

import json
from pathlib import Path

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.undertheradar import UndertheradarSettings, UtrVenue, cards, parse, vevents

FIXTURES = Path(__file__).parent / "fixtures" / "undertheradar"
TZ = "Asia/Jerusalem"
SHARABIYA = UtrVenue(
    name="שרביה", address="פנחס בן יאיר 3, תל אביב-יפו", city="תל אביב-יפו", latitude=32.0546245, longitude=34.7573914
)
SETTINGS = UndertheradarSettings(cron="53 10,22 * * *", venues={"sharabiya": SHARABIYA})


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _launch() -> dict:  # type: ignore[type-arg]
    return json.loads(_read("launch.json"))


def test_calendar_entries() -> None:
    (entry,) = vevents(_read("calendar.ics"))
    assert entry["DTSTART"] == ({"TZID": "Asia/Jerusalem"}, "20261204T153000")
    assert entry["LOCATION"][1] == "Sharabiya – Old Jaffa"


def test_home_card() -> None:
    card = cards(_read("home.html"), SETTINGS.site_url)["2026-12-04"]
    assert card["id"] == "jaffa-2026-12-04"
    assert card["poster"] == "https://undertheradar.events/jaffa-2026-12-04-lineup-square.jpg"
    assert card["description"].startswith("הפעם יוצאים למסע בין החומות של יפו העתיקה")


def test_party_with_its_lineup_and_tickets() -> None:
    (event,) = parse(_read("calendar.ics"), _read("home.html"), _launch(), SETTINGS, TZ)
    assert event.start_at is not None and event.start_at.isoformat() == "2026-12-04T15:30:00+02:00"
    assert event.end_at is not None and event.end_at.isoformat() == "2026-12-05T00:30:00+02:00"
    assert event.venue_name == "שרביה" and event.city == "תל אביב-יפו" and event.latitude == 32.0546245
    assert event.artists == ["SHMOOP", "AMIGDALA", "RITMO", "X-NOIZE", "NARKISSI"]
    assert event.description and "NARKISSI 23:00–00:30" in event.description
    assert event.extra["purchase_url"] == "https://easytix.co.il/event/u/UnderTheRadarOldJaffa"
    assert event.url.unicode_string() == "https://undertheradar.events/#jaffa-2026-12-04"


def test_before_the_sale_the_party_is_read_without_lineup() -> None:
    (event,) = parse(_read("calendar.ics"), _read("home.html"), None, SETTINGS, TZ)
    assert event.artists == [] and event.extra["purchase_url"] == "https://undertheradar.events"
    assert event.images == ["https://undertheradar.events/jaffa-2026-12-04-lineup-square.jpg"]


def test_enrichment_makes_it_a_psytrance_party_in_tel_aviv() -> None:
    (event,) = parse(_read("calendar.ics"), _read("home.html"), _launch(), SETTINGS, TZ)
    enriched = Enricher(Settings().enrichment, TZ).enrich(event)
    assert (
        enriched is not None
        and "psytrance" in enriched.genres
        and enriched.city == "תל אביב"
        and enriched.area == "jaffa"
    )
