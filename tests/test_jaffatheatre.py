"""Jaffa Theatre: the home page's TicketTool links with their sections, and a TicketTool page title."""

from __future__ import annotations

from pathlib import Path

import pytest

from partylinks.parsing import ParseError
from partylinks.scrapers.jaffatheatre import JaffaTheatreSettings, event_sections, parse_page

FIXTURES = Path(__file__).parent / "fixtures" / "jaffatheatre"
TZ = "Asia/Jerusalem"
SETTINGS = JaffaTheatreSettings(
    cron="18 7,19 * * *", venue_name="תיאטרון יפו", address="מפרץ שלמה 10, יפו", city="תל אביב-יפו",
    latitude=32.0548894, longitude=34.7539944,
)  # fmt: skip


def test_each_performance_once_with_its_section() -> None:
    sections = event_sections((FIXTURES / "home.html").read_text(encoding="utf-8"), SETTINGS.site_url)
    assert sections == {"920": "shows", "951": "libi-bamizrach"}  # the repeated link is read once


def test_title_gives_show_and_date() -> None:
    url = "https://new1.tickettool.net/web/event/event_id/887"
    event = parse_page((FIXTURES / "event_887.html").read_text(encoding="utf-8"), "887", url, SETTINGS, TZ)
    assert event.title == "תמונות מחיי המתים"
    assert event.start_at.isoformat() == "2026-12-03T20:30:00+02:00"
    assert (event.venue_name, event.latitude) == ("תיאטרון יפו", 32.0548894)
    assert event.images == ["https://new1.tickettool.net/images/event/887.jpg"]
    assert event.extra["purchase_url"] == url


def test_a_page_without_a_date_is_a_parse_error() -> None:
    with pytest.raises(ParseError):
        parse_page("<title>תיאטרון יפו</title>", "1", "https://x/1", SETTINGS, TZ)
