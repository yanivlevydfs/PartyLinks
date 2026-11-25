"""Eshkolot (eshkolot-il.com): festival pages of guided tours, their fest-*.js tables and sign-up forms."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.eshkolot import EshkolotSettings, geohash_point, map_point, parse_festival, tour_events

TZ = "Asia/Jerusalem"
IL = ZoneInfo(TZ)
FIXTURES = Path(__file__).parent / "fixtures" / "eshkolot"
HANUKA = (FIXTURES / "hanuka.html").read_text(encoding="utf-8")  # /hanuka (25.11.2026)
FEST = (FIXTURES / "fest-hanuka.js").read_text(encoding="utf-8")  # /js/fest-hanuka.js (25.11.2026)
MANHIGUT = (FIXTURES / "manhigut.html").read_text(encoding="utf-8")  # /manhigut (25.11.2026)
SITE = "https://www.eshkolot-il.com"
SETTINGS = EshkolotSettings.model_validate({"cron": "21 11,23 * * *", "cities": {"חצר כנרת": "כנרת", "ניצן": "ניצן"}})
TODAY = date(2026, 11, 25)


def _hanuka():
    fest = parse_festival(f"{SITE}/hanuka", HANUKA, FEST, SETTINGS, TODAY)
    assert fest is not None
    return fest


def test_hanuka_table_and_cards() -> None:
    fest = _hanuka()
    assert fest.group == "hanuka"
    assert fest.name == "חנוכה ישראלי"
    assert fest.holidays == ["חנוכה"]
    assert fest.days["1208"] == date(2026, 12, 8)
    assert not fest.free  # each tour has its price
    assert len(fest.tours) == 70
    assert fest.images == [f"{SITE}/img/m/r-hanuka-1920.webp"]
    events = [e for t in fest.tours for e in tour_events(fest, t, {}, SETTINGS, TZ)]
    assert len(events) == 146  # 64 tours on two days, 6 on three


def test_hanuka_tour_event() -> None:
    fest = _hanuka()
    tour = next(t for t in fest.tours if t.tid == "13")
    seats = {"hanuka-13-1208": 66, "hanuka-13-1209": 0}
    first, second = tour_events(fest, tour, seats, SETTINGS, TZ)
    assert first.title == "ניגונים בסמטאות"
    assert first.source_event_id == "hanuka-13-1208"
    assert str(first.url) == f"{SITE}/hanuka#t-13"
    assert first.start_at == datetime(2026, 12, 8, 10, 30, tzinfo=IL)
    assert first.end_at == datetime(2026, 12, 8, 13, 30, tzinfo=IL)
    assert first.venue_name == "מצפור המאירי"
    assert first.city == "צפת"
    assert first.latitude == pytest.approx(32.967, abs=0.001)
    assert first.longitude == pytest.approx(35.492, abs=0.001)
    assert first.extra["purchase_url"] == f"{SITE}/tashlum?t=13"
    assert first.min_price == 0 and first.max_price == Decimal(10)
    assert first.tickets[0].remaining == 66 and not first.is_sold_out
    assert second.is_sold_out  # 0 seats left on 9.12
    assert "רוח צפת" in (first.description or "")
    assert first.source_genres == ["חנוכה", "סיורים", "סיורי יום"]
    enriched = Enricher(Settings().enrichment, TZ).enrich(first)
    assert enriched is not None
    assert {"tours", "holidays"} <= set(enriched.genres)
    assert "israeli" not in enriched.genres  # "חנוכה ישראלי" is no Israeli music


def test_hanuka_places() -> None:
    fest = _hanuka()
    by_id = {t.tid: t for t in fest.tours}
    kinneret = tour_events(fest, by_id["44"], {}, SETTINGS, TZ)[0]
    assert kinneret.city == "כנרת"  # configured: the nearest big town is wrong for a village
    english = tour_events(fest, by_id["51"], {}, SETTINGS, TZ)[0]
    assert english.city == "ירושלים"  # "Jerusalem"
    assert english.min_price == 0 and english.max_price == Decimal(45)
    jaffa = tour_events(fest, by_id["52"], {}, SETTINGS, TZ)[0]
    assert jaffa.latitude == pytest.approx(32.0556, abs=0.001)  # a Waze ll= link
    evening = tour_events(fest, by_id["68"], {}, SETTINGS, TZ)
    assert [e.start_at.day for e in evening if e.start_at] == [7, 8, 9]  # the tours of 7.12 too
    assert evening[0].latitude is None  # a Waze place id: geocoded from the address
    assert evening[0].address == "חניון טיילת ארמון הנציב, ירושלים"


def test_manhigut_form_days_and_departures() -> None:
    fest = parse_festival(f"{SITE}/manhigut", MANHIGUT, None, SETTINGS, TODAY)
    assert fest is not None
    assert fest.group == "manhigut"
    assert fest.name == "שבילי מנהיגות ואחדות"
    assert fest.free
    assert fest.departures == ["1000", "1300"]
    assert list(fest.days.values()) == [date(2026, 12, 7), date(2026, 12, 8), date(2026, 12, 9)]
    tour = next(t for t in fest.tours if t.tid == "1")
    events = tour_events(fest, tour, {"manhigut-1-1207-1000": 40}, SETTINGS, TZ)
    assert len(events) == 6
    first = events[0]
    assert first.source_event_id == "manhigut-1-1207-1000"
    assert first.title == "הצריף הפתוח לכולם"
    assert first.start_at == datetime(2026, 12, 7, 10, 0, tzinfo=IL)
    assert first.end_at == datetime(2026, 12, 7, 13, 0, tzinfo=IL)  # "משך כל סיור 3 שעות"
    assert first.city == "ירושלים"
    assert first.venue_name == "כיכר פריז, ירושלים"
    assert first.tickets[0].is_free and first.tickets[0].remaining == 40
    assert first.extra["purchase_url"] == f"{SITE}/manhigut#fReg"
    assert first.source_genres == ["חנוכה", "סיורים"]  # its heading: "סיורים בחנוכה"
    assert f"{SITE}/img/fest/manhigut-flyer.webp" in first.images


def test_yearless_form_day_rolls_to_next_year() -> None:
    fest = parse_festival(f"{SITE}/manhigut", MANHIGUT, None, SETTINGS, date(2027, 2, 1))
    assert fest is not None
    assert fest.days["1207"] == date(2027, 12, 7)


def test_map_links() -> None:
    lat, lon = geohash_point("sv9hc2kwv") or (0.0, 0.0)
    assert lat == pytest.approx(31.775, abs=0.001) and lon == pytest.approx(35.2176, abs=0.001)
    assert map_point("https://waze.com/ul/hsv9hc2kwv")[0] == pytest.approx(31.775, abs=0.001)
    assert map_point("https://maps.google.com/maps?q=32.3458%2C35.5338&z=17") == (32.3458, 35.5338)
    assert map_point("https://ul.waze.com/ul?place=X&ll=32.05557320%2C34.75651310&navigate=yes") == (
        32.055573,
        34.756513,
    )
    assert map_point("https://ul.waze.com/ul?venue_id=22872360.228592527.118117") == (None, None)
    assert map_point("https://maps.app.goo.gl/RtfEfD9uDpjFJKXcA") == (None, None)
    assert map_point(None) == (None, None)


def test_page_without_cards() -> None:
    assert (
        parse_festival(f"{SITE}/", "<html><body><main><h1>x</h1></main></body></html>", None, SETTINGS, TODAY) is None
    )
