"""Tower of David: a package's dated slots, prices and post to events, on trimmed real API answers."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from partylinks.config import get_settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.towerofdavid import TowerOfDavidSettings, package_events

FIXTURES = Path(__file__).parent / "fixtures" / "towerofdavid"
TZ = "Asia/Jerusalem"
SETTINGS = TowerOfDavidSettings(
    cron="47 7,19 * * *", venue_name="מגדל דוד", address="שער יפו", city="ירושלים", latitude=31.776, longitude=35.228
)
PACKAGE = {"package_id": 4, "title": "החיזיון הלילי", "post_id": 1343}


def _load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def test_one_event_per_slot_with_prices_and_place() -> None:
    post, slots, prices = _load("post"), _load("slots"), _load("prices")
    events = package_events(PACKAGE, post, ["מופעי לילה"], slots, prices, "https://x/a.jpg", SETTINGS, TZ)
    assert [e.source_event_id for e in events] == ["18088", "18090"]
    first = events[0]
    assert first.title == "החיזיון הלילי"
    assert first.start_at.isoformat() == "2026-11-02T19:00:00+02:00"
    assert str(first.url) == "https://www.tod.org.il/event/the-night-spectacular/"
    assert (first.venue_name, first.latitude) == ("מגדל דוד", 31.776)
    assert first.min_price == Decimal(43) and first.max_price == Decimal(69) and not first.is_sold_out
    enriched = Enricher(get_settings().enrichment, TZ).enrich(first)
    assert enriched is not None and "live_show" in enriched.genres


def test_tour_elsewhere_in_the_city_has_no_museum_pin_and_no_price_list() -> None:
    post = _load("post")
    post["acf"]["location"] = ["off-site"]
    slots = {"events": {"05/11/2026": {"7": {"id": 7, "quantity": 0, "title": "10:30"}}}}
    (event,) = package_events(PACKAGE, post, [], slots, None, None, SETTINGS, TZ)
    assert event.venue_name is None and event.latitude is None and event.city == "ירושלים"
    assert event.is_sold_out and event.min_price is None and event.images == []
