"""SwapTix: a catalogue event and its resale listings, on a trimmed real answer."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from partylinks.scrapers.swaptix import SwapTixSettings, resale_events

FIXTURE = Path(__file__).parent / "fixtures" / "swaptix" / "aviv_geffen.json"
TZ = "Asia/Jerusalem"
SETTINGS = SwapTixSettings(cron="9 7,19 * * *")


def _load() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_listings_become_one_resale_event_per_start() -> None:
    data = _load()
    (event,) = resale_events(data["item"], data["listings"], SETTINGS, TZ)
    assert event.title == "אביב גפן"
    assert event.start_at is not None
    assert event.start_at.isoformat() == "2026-12-30T21:00:00+02:00"  # 19:00 UTC
    assert (event.venue_name, event.city) == ("היכל התרבות תל אביב, תל אביב", "תל אביב")
    assert [(t.name, t.price) for t in event.tickets] == [("ה' עליון שורה 32", Decimal(199))]
    assert event.min_price == Decimal(199) and event.extra["resale"] is True
    assert str(event.url).startswith("https://swaptix.co.il/events/evt_is_")


def test_no_tickets_on_sale_no_event() -> None:
    data = _load()
    sold = [{**row, "quantityAvailable": 0} for row in data["listings"]]
    assert resale_events(data["item"], sold, SETTINGS, TZ) == []
    assert resale_events(data["item"], [], SETTINGS, TZ) == []
