"""Live Events parser test against the start of a real month table (June 2027)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from partylinks.scrapers import livevents

TABLE = (Path(__file__).parent / "fixtures" / "livevents" / "june.html").read_text(encoding="utf-8")
CALENDAR = "https://livevents.co.il/events/"


def test_livevents_rows() -> None:
    events = livevents.parse_table(TABLE, CALENDAR, "Asia/Jerusalem")
    assert len(events) == 5 + 11  # five single-date rows, Oasis in Manchester on eleven dates
    rod = events[0]
    assert (rod.title, rod.city, rod.country, rod.artists) == ("רוד סטיוארט", "ברלין", "DE", ["רוד סטיוארט"])
    assert rod.start_at == datetime(2027, 6, 1, tzinfo=ZoneInfo("Asia/Jerusalem"))
    assert (str(rod.url), rod.currency, rod.min_price, rod.extra) == (
        "https://livevents.co.il/package/rod-stewart-ber-gold/",
        "EUR",
        Decimal(949),
        {"package": True},
    )
    quote = events[1]  # "request a quote": no price, the calendar is the link
    assert (quote.title, quote.city, quote.country, str(quote.url), quote.min_price) == (
        "ניקלבק",
        "מנצ'סטר",
        "GB",
        CALENDAR,
        None,
    )
    oasis = [e for e in events if e.title == "אואזיס"]
    assert len({e.source_event_id for e in oasis}) == 11 and {(e.currency, e.min_price) for e in oasis} == {
        ("GBP", Decimal(1449))
    }
