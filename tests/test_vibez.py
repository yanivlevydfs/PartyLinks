"""Vibez parser tests against real API answers (October 2026): event details and their products."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.models import TierStatus
from partylinks.scrapers import vibez

ITEMS = json.loads((Path(__file__).parent / "fixtures" / "vibez" / "events.json").read_text(encoding="utf-8"))
SITE, TZ = "https://web.vibez.io", "Asia/Jerusalem"


def parse(name: str) -> vibez.ScrapedEvent:
    item = next(i for i in ITEMS if i["event"]["name"].startswith(name))
    event = vibez.parse_event(item["event"], item["products"], SITE, TZ)
    assert event is not None
    return event


def test_event_with_address_tiers_and_link() -> None:
    event = parse("dopamine - Masquerade")
    assert str(event.url) == f"{SITE}/events/60b56526-fb1f-4ce3-b63c-20901f9bda8e"
    assert event.address == "מנשה בן ישראל 17 , תל אביב" and event.venue_name is None
    assert event.organizer == "dopamine" and event.description and event.image_url
    assert event.min_price == Decimal("160.00")
    assert event.tickets[0].status is TierStatus.SOLD_OUT  # "left": 0


def test_venue_lineup_and_genres_from_the_text() -> None:
    settings = Settings()
    enricher = Enricher(settings.enrichment, settings.timezone)
    party = enricher.enrich(parse("Back to the 90"))
    assert party is not None and party.venue_name == "Duplex Club"
    assert "throwback" in party.genres and any("DJ" in name for name in party.lineup)
    dance = enricher.enrich(parse("Tribal Ecstatic"))
    assert dance is not None and "yoga_wellness" in dance.genres


def test_add_ons_are_no_tickets_and_country_is_no_place() -> None:
    sabaya = parse("Sabaya")  # buffet and child ticket; the buffet is food, not entry
    assert [t.name for t in sabaya.tickets] == ["ילד עד גיל 10"]
    assert parse("Shifting").venue_name is None  # "ישראל"


def test_directions_are_no_place() -> None:
    item = {"id": "x", "name": "Rave", "start_time": "2026-11-01T20:00:00Z", "status": "active"}
    for place in ("50 דק צפונית מתל אביב", "55 min from tel aviv - north", "שעה וחצי דרומה מהמרכז"):
        event = vibez.parse_event({**item, "location": place}, [], SITE, TZ)
        assert event is not None and event.venue_name is None and event.address is None, place


def test_a_sale_window_is_no_end() -> None:
    sabaya = parse("Sabaya")  # "ends" a year later: the series' sale window
    assert sabaya.end_at is None
    assert parse("dopamine - Masquerade").end_at is not None


def test_country_of_an_event_abroad() -> None:
    item = {"id": "x", "name": "Full Moon", "start_time": "2026-11-05T20:00:00Z", "status": "active"}

    def country(zone: str, place: str, currency: str = "ILS") -> str | None:
        event = vibez.parse_event({**item, "timezone": zone, "location": place, "project": {"currency": currency}}, [], SITE, TZ)
        assert event is not None
        return event.country

    # Real Vibez events, October 2026.
    assert country("Asia/Bangkok", "LOST JUNGLE", "THB") == "TH"
    assert country("Asia/Bangkok", "Thong Not Bay, Tycoon's Place, Nakhon Si Thammarat, Thailand", "THB") == "TH"
    assert country("Europe/Moscow", "Greece", "EUR") == "GR"  # the place wins over a wrong zone
    assert country("Indian/Mahe", "YEHUDA HALEVI 19") is None  # shekels: a wrong zone, in Israel
    assert country("Asia/Hebron", "מצפה") is None and country("Asia/Jerusalem", "Duplex Club") is None
