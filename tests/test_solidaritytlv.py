"""Solidarity festival: the industry-events repeater and the conference page, trimmed real answers (27.11.2026)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.solidaritytlv import SolidarityPage, SolidaritySettings, day_event, repeater_events

FIXTURES = Path(__file__).parent / "fixtures" / "solidaritytlv"
TZ = "Asia/Jerusalem"
SITE = "https://www.solidaritytlv.org"
SETTINGS = SolidaritySettings(cron="19 6,18 * * *")
CONFERENCE = SolidarityPage(path="/conference", kind="day", title="כנס סולידריות לקולנוע וזכויות אדם", free=True)


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_industry_events_one_per_visible_item() -> None:
    events = repeater_events(_read("industry-events.html"), SITE + "/industry-events", SETTINGS, TZ)
    assert [(e.title, e.start_at.isoformat() if e.start_at else None, e.venue_name) for e in events] == [
        ("זכויות אדם אמיתי במציאות בינה מלאכותית", "2026-12-13T13:30:00+02:00", "סינמטק תל אביב"),
        ("דור חדש של קולנוענים מהחברה הערבית", "2026-12-13T18:00:00+02:00", "סינמטק תל אביב"),
    ]
    first = events[0]
    assert first.source_event_id == "5a2df400-398f-4d7c-9bcc-f15241adcd2c"
    assert first.description and first.description.startswith("אולם 6\n\nפאנל העוסק בהשפעת הבינה המלאכותית")
    assert first.latitude == 32.0707072 and first.longitude == 34.7834524
    assert first.images == ["https://static.wixstatic.com/media/c8ddcc_5bda8a28631b40e8bfd049a2a4d9625f~mv2.jpg"]
    assert first.city == "תל אביב-יפו" and str(first.url).endswith("#5a2df400-398f-4d7c-9bcc-f15241adcd2c")


def test_conference_day_reads_rtl_hours_in_order() -> None:
    [event] = day_event(_read("conference.html"), SITE + "/conference", CONFERENCE, SETTINGS, TZ, date(2026, 11, 27))
    assert event.title == "כנס סולידריות לקולנוע וזכויות אדם"
    assert event.start_at is not None and event.start_at.isoformat() == "2026-12-13T15:30:00+02:00"
    assert event.end_at is not None and event.end_at.isoformat() == "2026-12-13T21:30:00+02:00"
    assert event.venue_name == "סינמטק תל אביב" and event.description and event.description.startswith("אולם 6")
    assert event.min_price == 0 and event.tickets[0].is_free


def test_conference_year_rolls_after_the_date() -> None:
    [event] = day_event(_read("conference.html"), SITE + "/conference", CONFERENCE, SETTINGS, TZ, date(2027, 1, 5))
    assert event.start_at is not None and event.start_at.year == 2027


def test_enrichment_gives_genres() -> None:
    [event] = day_event(_read("conference.html"), SITE + "/conference", CONFERENCE, SETTINGS, TZ, date(2026, 11, 27))
    enriched = Enricher(Settings().enrichment, TZ).enrich(event)
    assert enriched is not None and enriched.genres and enriched.city == "תל אביב"
