"""TikTik parser test against the JSON-LD of two real product pages (a ticket and a package)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from partylinks.scrapers import tiktik

FIXTURES = Path(__file__).parent / "fixtures" / "tiktik"
SITE = "https://www.tiktik-online.co.il/product/"


def _parse(name: str, slug: str) -> tiktik.ScrapedEvent:
    event = tiktik.parse_product((FIXTURES / f"{name}.html").read_text(encoding="utf-8"), SITE + slug + "/", "Asia/Jerusalem")
    assert event is not None
    return event


def test_tiktik_ticket() -> None:
    e = _parse("sting", "sting-las-vegas-23-10-2027")
    assert e.source_event_id == "116773" and e.title == "סטינג לאס וגאס 23.10.27"
    assert e.start_at == datetime(2027, 10, 23, 20, tzinfo=timezone(timedelta(hours=-7)))
    assert (e.venue_name, e.city, e.country) == ("The Colosseum at Caesars Palace", "Las Vegas", "US")
    assert (e.currency, e.min_price, e.max_price) == ("EUR", Decimal(288), Decimal(1134))
    assert e.artists == ["סטינג"] and not e.is_sold_out and e.extra["package"] is False


def test_tiktik_package_date_only() -> None:
    e = _parse("gracie", "gracieberlin")
    assert e.source_event_id == "84440" and e.extra["package"] is True
    assert e.start_at == datetime(2027, 5, 12, tzinfo=ZoneInfo("Asia/Jerusalem")) and e.end_at is None
    assert (e.venue_name, e.city, e.country) == ("Uber Arena", "ברלין", "DE")


def test_tiktik_page_without_event() -> None:
    assert tiktik.parse_product("<html></html>", SITE + "gift/", "Asia/Jerusalem") is None


def test_tiktik_page_without_country_is_still_abroad() -> None:
    """Some pages (Westlife Belfast) give only the city: still abroad, never in the Israeli feed."""
    page = (FIXTURES / "sting.html").read_text(encoding="utf-8").replace('"addressCountry": "US"', '"x": 0')
    e = tiktik.parse_product(page, SITE + "x/", "Asia/Jerusalem")
    assert e is not None and e.city == "Las Vegas" and e.country == "ZZ"


def test_tiktik_page_without_offers_uses_listing() -> None:
    """About one page in six has no Product/offers block: id, price and stock come from the Store API."""
    page = (FIXTURES / "sting.html").read_text(encoding="utf-8")
    page = page.replace('"@type":"Product"', '"@type":"Thing"').replace('"offers": {', '"x": {')
    listed = {"id": 116773, "is_in_stock": True, "prices": {"price": "28800", "currency_minor_unit": 2}}
    e = tiktik.parse_product(page, SITE + "sting-las-vegas-23-10-2027/", "Asia/Jerusalem", listed)
    assert e is not None and e.source_event_id == "116773"
    assert (e.currency, e.min_price, e.max_price) == ("EUR", Decimal(288), Decimal(288)) and not e.is_sold_out


def test_tiktik_match_is_sport() -> None:
    """Sport products carry a SportsEvent: tagged sport, the teams are not artists."""
    page = (FIXTURES / "sting.html").read_text(encoding="utf-8").replace('"MusicEvent"', '"SportsEvent"')
    e = tiktik.parse_product(page, SITE + "x/", "Asia/Jerusalem")
    assert e is not None and e.source_genres == ["ספורט"] and e.artists == []
    from partylinks.config import Settings
    from partylinks.enrichment import Enricher

    assert "sport" in Enricher(Settings().enrichment, "Asia/Jerusalem")._detect_genres(e)
