"""LiveTickets parser tests against real listing pages (a city and the festivals page) and home links, trimmed."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from partylinks.scrapers import livetickets

FIXTURES = Path(__file__).parent / "fixtures" / "livetickets"
SITE = "https://www.livetickets.co.il/"
EVENT_URL = SITE + "events/events.aspx?eid={eid}"


def _rows(name: str) -> list[livetickets.ScrapedEvent]:
    return livetickets.parse_rows((FIXTURES / name).read_text(encoding="utf-8"), EVENT_URL, "Asia/Jerusalem")


def test_livetickets_city_rows() -> None:
    match, tentative, concert = _rows("berlin.html")
    assert (match.source_event_id, match.title, str(match.url)) == (
        "2326352",
        "הרטה ברלין - ארמיניה בילפלד",
        SITE + "events/events.aspx?eid=2326352",
    )
    assert match.start_at == datetime(2026, 10, 24, 13, tzinfo=ZoneInfo("Europe/Berlin"))  # the venue's local time
    assert (match.venue_name, match.city, match.country) == ("האצטדיון האולימפי ברלין", "ברלין", "DE")
    assert (match.currency, match.min_price, match.extra) == ("EUR", Decimal(85), {})
    assert tentative.extra == {"date_not_final": True}
    assert (concert.title, concert.start_at.month, concert.min_price) == ("Aespa", 1, Decimal(300))


def test_livetickets_festival_rows_dollar_and_no_price() -> None:
    tomorrowland, sziget = _rows("festivals.html")
    assert (tomorrowland.city, tomorrowland.country, tomorrowland.currency, tomorrowland.min_price) == (
        "Chon Buri",
        "TH",
        "USD",
        Decimal(690),
    )
    assert (sziget.country, sziget.min_price) == ("HU", None)


def test_livetickets_listing_links() -> None:
    links = livetickets.listing_links((FIXTURES / "home.html").read_text(encoding="utf-8"), SITE)
    # /search/ (robots-disallowed) and event links are not listings; the two casings of a page are one.
    assert sorted(links.values()) == [
        SITE + "Category1/CONCERTS.aspx",
        SITE + "DT_performers/Festivals.aspx",
        SITE + "Dt_performers/Hanuka.aspx",
        SITE + "cities/Germany_Berlin_Berlin.aspx",
        SITE + "performers/AC%20Milan.aspx",
    ]


def test_livetickets_category_names():
    assert livetickets.category_name("https://www.livetickets.co.il/Category1/CONCERTS.aspx") == "concerts"
    assert livetickets.category_name("https://www.livetickets.co.il/Category2/SOCCER.aspx") == "soccer"
    assert livetickets.category_name("https://www.livetickets.co.il/Category3/English%20Premier%20League.aspx") == "soccer"
