"""Teder (Beit Romano): homepage cards and event pages to ScrapedEvent."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from partylinks.scrapers.teder import TederSettings, parse_event, parse_listing

TZ = "Asia/Jerusalem"
FIXTURES = Path(__file__).parent / "fixtures/teder"
SETTINGS = TederSettings.model_validate(
    yaml.safe_load((Path(__file__).parents[1] / "config/settings.yaml").read_text("utf-8"))["sources"]["teder"]
)


def _page(name: str) -> str:
    return (FIXTURES / name).read_text("utf-8")


def test_listing_reads_event_cards() -> None:
    assert parse_listing(_page("home.html"), SETTINGS) == [
        "https://www.teder.fm/events/15355",
        "https://www.teder.fm/events/15332",
        "https://www.teder.fm/events/15334",
    ]


def test_free_night_in_rafi() -> None:
    e = parse_event(_page("event_15332.html"), "https://www.teder.fm/events/15332", SETTINGS, TZ)
    assert e.title == "Rasco" and e.source_event_id == "15332"
    assert e.start_at is not None and e.start_at.isoformat() == "2026-10-21T20:00:00+03:00"
    assert e.extra["room"] == "רפי" and e.venue_name == SETTINGS.venue_name
    assert [(t.price, t.is_free) for t in e.tickets] == [(Decimal(0), True)]
    assert e.description and "פילינג או" in e.description
    assert "ממליצים גם על זה" not in e.description  # the related-shows strip is not the description
    assert e.images and e.images[0].startswith("https://www.teder.fm/images/")


def test_open_air_rave() -> None:
    e = parse_event(_page("event_15355.html"), "https://www.teder.fm/events/15355", SETTINGS, TZ)
    assert e.title == "RESTLESS" and e.extra["room"] == "רחבה"
    assert e.start_at is not None and e.start_at.isoformat() == "2026-10-24T14:00:00+03:00"
    assert e.min_price == 0
