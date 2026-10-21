"""Luma: listing page ids and /event/get to ScrapedEvent."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from partylinks.scrapers.luma import listed_ids, parse_event

TZ = "Asia/Jerusalem"
SITE = "https://luma.com"
FIXTURES = Path(__file__).parent / "fixtures/luma"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text("utf-8"))


def test_listing_keeps_in_person_events_in_israel() -> None:
    page = _load("page.json")
    assert listed_ids(page) == ["evt-Kf6PpQNGbjAXA3B", "evt-z1tlnqsA1x4PJMm", "evt-TiPMKw8LyqNa2ZC"]
    page["entries"][0]["event"]["geo_address_info"]["country_code"] = "JO"
    page["entries"][1]["event"]["location_type"] = "online"
    assert listed_ids(page) == ["evt-TiPMKw8LyqNa2ZC"]


def test_free_event_with_shown_address() -> None:
    e = parse_event(_load("evt-Kf6PpQNGbjAXA3B.json"), SITE, TZ)
    assert e is not None and e.url.unicode_string() == "https://luma.com/vhi7dbcl"
    assert e.title == "EcoValue Forum Gathering @ PLANETech Week"
    assert e.venue_name == "בית קפה נורה - Nora Cafe" and e.address == "שלמה אבן גבירול 71" and e.city == "תל אביב-יפו"
    assert e.latitude and abs(e.latitude - 32.0823) < 0.001
    assert e.organizer == "EcoValue Forum"  # personal calendar: the host
    assert e.description and e.description.startswith("A new peer forum") and "\n" in e.description
    assert [t.kind.value for t in e.tickets] == ["free"] and e.min_price == 0
    assert e.event_types == ["Climate"] and e.source_genres == []


def test_paid_event_in_dollars() -> None:
    e = parse_event(_load("evt-80NFgdyIp4er616.json"), SITE, TZ)
    assert e is not None and e.currency == "USD" and e.min_price == Decimal(30)
    assert e.tickets[0].currency == "USD" and e.tickets[0].name == "Standard"


def test_online_event_is_skipped() -> None:
    detail = _load("evt-Kf6PpQNGbjAXA3B.json")
    detail["event"]["location_type"] = "online"
    assert parse_event(detail, SITE, TZ) is None
