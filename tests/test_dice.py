"""DICE: browse-page rows to ScrapedEvent."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from partylinks.config import get_settings
from partylinks.enrichment import Enricher
from partylinks.parsing import ParseError
from partylinks.scrapers.dice import browse_rows, parse_row

TZ = "Asia/Jerusalem"
ROW = {
    "id": "6af6e5fac4992d0001b4d821",
    "name": "Labyrinth NYE: Daphni All Night Long",
    "status": "on-sale",
    "images": {"square": "https://dice-media.imgix.net/attachments/2026-11-04/a.jpg?rect=0%2C0%2C1080%2C1080"},
    "dates": {
        "timezone": "Europe/London",
        "event_start_date": "2026-12-31T22:00:00+00:00",
        "event_end_date": "2027-01-01T06:00:00+00:00",
    },
    "venues": [
        {
            "name": "Colour Factory",
            "address": "Queen's Yard, London E9 5EN",
            "city": {
                "name": "London",
                "country_code": "GB",
                "location": {"lat": 51.507653, "lng": -0.107722, "place": "Europe/London"},
            },
        }
    ],
    "price": {"currency": "GBP", "amount": None, "amount_from": 5379},
}


def test_browse_page_rows() -> None:
    page = (
        '<script id="__NEXT_DATA__" type="application/json">'
        + json.dumps({"props": {"pageProps": {"events": [ROW, {"name": "no id"}]}}})
        + "</script>"
    )
    assert [r["id"] for r in browse_rows(page)] == [ROW["id"]]
    with pytest.raises(ParseError):
        browse_rows("<html></html>")


def test_row_in_the_venue_zone_priced_from_minor_units_without_the_city_centre() -> None:
    event = parse_row(ROW, ["party", "dj set", "party"], "https://dice.fm", TZ)
    assert event is not None
    london = ZoneInfo("Europe/London")
    assert str(event.url) == "https://dice.fm/event/6af6e5fac4992d0001b4d821"
    assert event.start_at == datetime(2026, 12, 31, 22, tzinfo=london)
    assert event.end_at == datetime(2027, 1, 1, 6, tzinfo=london)
    assert (event.country, event.time_zone, event.city) == ("GB", "Europe/London", "London")
    assert (event.venue_name, event.address) == ("Colour Factory", "Queen's Yard, London E9 5EN")
    assert event.latitude is None  # the row's coordinates are the city's centre
    assert (event.min_price, event.currency, event.is_sold_out) == (Decimal("53.79"), "GBP", False)
    assert event.source_genres == ["party", "dj set"]
    settings = get_settings()
    assert Enricher(settings.enrichment, settings.timezone).enrich(event).genres


def test_sold_out_and_unpriced_rows() -> None:
    sold = parse_row(
        ROW | {"status": "sold-out", "price": {"currency": "GBP", "amount_from": None}},
        ["concert"],
        "https://dice.fm",
        TZ,
    )
    assert sold is not None and sold.is_sold_out and sold.min_price is None and sold.tickets == []
    assert parse_row(ROW | {"name": " "}, ["party"], "https://dice.fm", TZ) is None
