"""Issta parser test against the real tickets page (October 2026: 20 package cards)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from partylinks.scrapers import issta

PAGE = (Path(__file__).parent / "fixtures" / "issta" / "tickets.html").read_text(encoding="utf-8")
SITE = "https://www.issta.co.il"
TZ = ZoneInfo("Asia/Jerusalem")


def test_issta_one_event_per_trip() -> None:
    events = issta.parse_listing(PAGE, SITE, "Asia/Jerusalem")
    by_id = {e.source_event_id: e for e in events}
    assert len(events) == 17  # 20 cards; three trips are sold as two packages each
    stevie = by_id["7887"]
    assert (stevie.title, stevie.city, stevie.country, stevie.currency) == (
        "סטיבי וונדר בלונדון",
        "London",
        "GB",
        "GBP",
    )
    assert (stevie.start_at, stevie.end_at) == (datetime(2026, 11, 4, tzinfo=TZ), datetime(2026, 11, 8, tzinfo=TZ))
    assert str(stevie.url) == f"{SITE}/loader?url=/sport/details?sid=3&vid=15&pid=7887&dport=LON&fdate=04/11/2026"
    assert stevie.extra == {"package": True, "airport": "LON", "packages": ["7887"]}
    rieu = by_id["7943"]  # two packages of one trip: the cheaper one is the event, both set the range
    assert (rieu.min_price, rieu.max_price, rieu.extra["packages"]) == (Decimal(849), Decimal(999), ["7943", "7942"])
    assert (rieu.city, rieu.country, rieu.currency) == ("Athens", "GR", "EUR")


def test_issta_card_days_win_over_the_links_date() -> None:
    oasis = next(e for e in issta.parse_listing(PAGE, SITE, "Asia/Jerusalem") if e.source_event_id == "7953")
    assert oasis.start_at == datetime(2027, 9, 17, tzinfo=TZ)  # link says 04/06/2027, the card 17/09


def test_destination_codes() -> None:
    assert issta.destination("PAR") == ("Paris", "FR")
    assert issta.destination("BCN") == ("Barcelona", "ES")
    assert issta.destination("???") == (None, "ZZ")
