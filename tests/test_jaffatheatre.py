"""Jaffa Theatre: the home page's TicketTool links with their sections, and a TicketTool page title."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from partylinks.parsing import ParseError
from partylinks.scrapers.jaffatheatre import JaffaTheatreSettings, event_sections, parse_page, tickets

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
    assert [(t.name, t.price) for t in event.tickets] == [("קטגוריה 1", Decimal("80.00"))]  # seated: the legend


def test_unseated_show_price_from_its_ticket_counter() -> None:
    page = (FIXTURES / "event_908.html").read_text(encoding="utf-8")
    assert [(t.name, t.price) for t in tickets(page)] == [("קטגוריה 1", Decimal("100.00"))]


def test_each_price_zone_once() -> None:  # event 914: two zones in the legend
    page = (
        '<div class="carus2"><div class="block">ILS</div>'
        '<div data-zone-id="1" data-name="95"><i></i>95.00</div>'
        '<div data-zone-id="2" data-name="קטגוריה 1"><i></i>110.00</div></div>'
    )
    assert [t.price for t in tickets(page)] == [Decimal("95.00"), Decimal("110.00")]


def test_a_page_without_a_date_is_a_parse_error() -> None:
    with pytest.raises(ParseError):
        parse_page("<title>תיאטרון יפו</title>", "1", "https://x/1", SETTINGS, TZ)
