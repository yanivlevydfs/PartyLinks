"""Ticket Place parser test against two real product pages (a concert and a festival), trimmed."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from partylinks.scrapers import ticketplace

FIXTURES = Path(__file__).parent / "fixtures" / "ticketplace"
URL = "https://ticketplace.co.il/product/x/"


def _parse(name: str, **kwargs: bool) -> ticketplace.ScrapedEvent:
    event = ticketplace.parse_product(
        (FIXTURES / f"{name}.html").read_text(encoding="utf-8"), URL, "Asia/Jerusalem", **kwargs
    )
    assert event is not None
    return event


def test_ticketplace_concert() -> None:
    e = _parse("dylan")
    assert e.source_event_id == "22064" and e.title == "בוב דילן בוינה" and e.artists == ["בוב דילן"]
    assert e.start_at == datetime(2026, 11, 12, 20, tzinfo=ZoneInfo("Europe/Paris"))  # Vienna's local time
    assert (e.venue_name, e.city, e.country) == ("Wiener Stadthalle", "וינה", "AT")
    assert (e.currency, e.min_price, e.max_price, e.is_sold_out) == ("EUR", Decimal(260), Decimal(310), False)
    assert e.image_url and e.image_url.endswith(".jpg")


def test_ticketplace_festival_english_country() -> None:
    e = _parse("pinkpop", festival=True)
    assert (e.title, e.venue_name, e.city, e.country) == ("פסטיבל פינקפופ", None, "Landgraaf", "NL")
    assert e.source_genres == ["פסטיבל"] and e.artists == [] and e.min_price == Decimal(450)


def test_ticketplace_page_without_event() -> None:
    assert ticketplace.parse_product("<html></html>", URL, "Asia/Jerusalem") is None
