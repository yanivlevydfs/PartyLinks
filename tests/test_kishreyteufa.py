"""Kishrey Teufa feed test against real rows of the embedded allEventsJsonLite catalogue."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from partylinks.scrapers import kishreyteufa

FIXTURE = Path(__file__).parent / "fixtures" / "kishreyteufa" / "show.html"
SEARCH = "https://www.kishrey-teufa.co.il/searchEvent.do"


def _events() -> dict[str, kishreyteufa.ScrapedEvent]:
    rows = kishreyteufa.parse_feed(FIXTURE.read_text(encoding="utf-8"))
    assert len(rows) == 4  # one is a football match
    return {
        e.source_event_id: e
        for r in rows
        if r["categoryid"] == 2
        for e in [kishreyteufa.parse_row(r, SEARCH, "Asia/Jerusalem")]
    }


def test_kishreyteufa_concert_in_britain() -> None:
    e = _events()["2327481"]
    assert (e.title, e.artists, e.source_genres) == ("דוראן דוראן", ["דוראן דוראן"], ["פופ / רוק"])
    assert e.start_at == datetime(2026, 10, 23, 18, tzinfo=ZoneInfo("Europe/London"))
    assert (e.venue_name, e.city, e.country) == ("M&S Bank Arena", "ליברפול", "GB")
    assert (e.currency, e.min_price, e.is_sold_out) == ("GBP", Decimal(195), False)
    assert parse_qs(urlsplit(str(e.url)).query)["tourmentId"] == ["329"]


def test_kishreyteufa_festival_lineup() -> None:
    events = _events()
    assert set(events) == {"2322852", "2327481", "2327751"}
    e = events["2327751"]
    assert e.title == "פסטיבל פרימוורה - ברצלונה" and e.country == "ES" and e.currency == "EUR"
    assert e.artists[:2] == ["פסטיבל פרימוורה", "פונטיינס די סי"]  # empty names dropped
