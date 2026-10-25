"""Poalim Atarim: the Tickchak hub's site list and the sites' Tickchak pages to ScrapedEvent
(fixtures trimmed from the May 2026 campaign, fetched October 2026)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from partylinks.parsing import next_data
from partylinks.scrapers.poalimatarim import PoalimAtarimSettings, opening_year, parse_listing, parse_site

TZ = "Asia/Jerusalem"
FIXTURES = Path(__file__).parent / "fixtures/poalimatarim"
SETTINGS = PoalimAtarimSettings(
    cron="54 9 * * *",
    site_url="https://poalimatarim.com/",
    event_url_template="https://tickchak.co.il/{eid}",
    details_selector=".details2-textbox",
    hours_heading="ימי ושעות הפעילות",
    directions_heading="דרכי הגעה",
    organizer="בנק הפועלים",
)
BEFORE_CAMPAIGN = date(2026, 5, 1)


def _page(name: str) -> str:
    return (FIXTURES / name).read_text("utf-8")


def _sites() -> dict[int, dict]:
    return {site["eid"]: site for site in parse_listing(next_data(_page("hub.html")))}


def test_listing_skips_sites_hidden_by_the_hub() -> None:
    assert list(_sites()) == [41203, 100820, 41208]  # 41221 is in eventsRemovedFromPage


def test_one_event_per_opening_day() -> None:
    events = parse_site(_page("site_41203.html"), _sites()[41203], SETTINGS, TZ, BEFORE_CAMPAIGN)
    assert [e.source_event_id for e in events] == [
        "41203-20260508",
        "41203-20260509",
        "41203-20260515",
        "41203-20260516",
        "41203-20260529",
        "41203-20260530",
    ]
    e = events[1]
    assert (e.start_at.isoformat(), e.end_at.isoformat()) == ("2026-05-09T10:00:00+03:00", "2026-05-09T17:00:00+03:00")
    assert e.venue_name == "אנו – מוזיאון העם היהודי"
    assert e.address == "קמפוס אוניברסיטת תל-אביב, רחוב קלאוזנר 15, רמת אביב"
    assert (e.latitude, e.longitude) == (32.11412033, 34.80528831)
    assert str(e.url).startswith("https://tickchak.co.il/41203?")
    assert [(t.price, t.is_free) for t in e.tickets] == [(Decimal(0), True)]
    assert e.images and e.description and "קודקס ששון" in e.description


def test_ticket_types_from_offers() -> None:
    events = parse_site(_page("site_100820.html"), _sites()[100820], SETTINGS, TZ, BEFORE_CAMPAIGN)
    assert len(events) == 3 and events[0].start_at.isoformat() == "2026-05-08T09:00:00+03:00"
    assert [t.is_sold_out for t in events[0].tickets] == [True, True, False]
    assert not events[0].is_sold_out and events[0].min_price == 0


def test_past_days_are_dropped() -> None:
    assert parse_site(_page("site_41203.html"), _sites()[41203], SETTINGS, TZ, date(2026, 10, 25)) == []


def test_year_rolls_over_only_near_new_year() -> None:
    assert opening_year(date(2026, 1, 8), date(2026, 12, 20)) == 2027
    assert opening_year(date(2026, 5, 8), date(2026, 12, 20)) == 2026
    assert opening_year(date(2026, 5, 8), date(2026, 5, 1)) == 2026
