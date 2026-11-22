"""Hangar 11 (hangar11.co.il) tests against its real list-view iCal feed and one event page (Nov 2026)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.hangar11 import Hangar11Settings, page_prices, parse_feed, ticket_link, with_page

FIXTURES = Path(__file__).parent / "fixtures" / "hangar11"
TZ = "Asia/Jerusalem"
SETTINGS = Hangar11Settings(
    cron="20 9,21 * * *", address="נמל תל אביב", city="תל אביב-יפו", latitude=32.0964756, longitude=34.7740091
)


def _feed() -> list:
    return parse_feed((FIXTURES / "list.ics").read_text(encoding="utf-8"), SETTINGS, TZ)


def test_feed_lists_every_upcoming_event() -> None:
    events = _feed()
    assert len(events) == 12
    mergui = next(e for e in events if e.title == "מרגי בהאנגר 11")
    assert mergui.source_event_id == "6954"
    assert mergui.start_at == datetime(2026, 12, 26, 21, 30, tzinfo=ZoneInfo(TZ)) and mergui.time_known
    assert mergui.end_at is None  # the site repeats the start as the end
    assert (mergui.venue_name, mergui.address, mergui.city) == ("האנגר 11", "נמל תל אביב", "תל אביב-יפו")
    assert (mergui.latitude, mergui.longitude) == (32.0964756, 34.7740091)
    assert mergui.images == ["https://hangar11.co.il/events/wp-content/uploads/2026/09/מרגי-1200-650-hangar.jpg"]
    assert mergui.source_genres == []  # the feed's CATEGORIES are every tag of the site: not used
    assert mergui.description and mergui.description.startswith("מרגי חוזר להאנגר 11!")


def test_duplicated_draft_title_loses_copy() -> None:
    titles = [e.title for e in _feed()]
    assert titles.count("אברהם טל • מוש בן ארי | המופע המשותף") == 2
    assert not any(t.lower().endswith("copy") for t in titles)


def test_club_hall_kept_as_hall() -> None:
    halls = {e.extra.get("hall") for e in _feed()}
    assert "האנגר 11" in halls


def test_event_page_ticket_link_and_price() -> None:
    page = (FIXTURES / "mergui.html").read_text(encoding="utf-8")
    assert ticket_link(page, SETTINGS) == "http://to-mix.co.il/product/mergui_hangar11/?eventbuzz=true"
    mergui = next(e for e in _feed() if e.source_event_id == "6954")
    full = with_page(mergui, page, SETTINGS)
    assert full.extra["purchase_url"].startswith("http://to-mix.co.il/")
    assert (full.min_price, full.max_price) == (Decimal(169), Decimal(259))

    settings = Settings()
    enriched = Enricher(settings.enrichment, settings.timezone).enrich(full)
    assert enriched is not None and enriched.genres


def test_page_prices_and_missing_ticket_block() -> None:
    assert page_prices("169₪ – 259₪") == [Decimal(169), Decimal(259)]
    assert page_prices("₪ 120") == [Decimal(120)]
    assert page_prices("חינם") == []
    assert ticket_link("<html><a href='https://go-out.co/x'>x</a></html>", SETTINGS) is None
    page = '<div id="tickets"></div><a href="https://www.facebook.com/x">fb</a><a href="https://www.eventer.co.il/e/1">buy</a>'
    assert ticket_link(page, SETTINGS) == "https://www.eventer.co.il/e/1"
