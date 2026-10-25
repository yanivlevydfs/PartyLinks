"""HaEzor and Levontin 7 parsers against their real pages (October 2026)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from partylinks.config import Settings
from partylinks.scrapers import haezor, levontin7

FIXTURES = Path(__file__).parent / "fixtures"
TZ = "Asia/Jerusalem"


def test_haezor_calendar_and_show_page() -> None:
    cfg = haezor.HaEzorSettings.model_validate(Settings().sources["haezor"])
    cards = haezor.parse_listing((FIXTURES / "haezor" / "gigs.html").read_text(encoding="utf-8"))
    assert len(cards) == 9
    events = [haezor.parse_card(c, cfg, TZ, today=date(2026, 10, 25)) for c in cards]
    tierra = next(e for e in events if e and e.title == "Tierra Latina")
    assert tierra.start_at == datetime(2026, 10, 28, 20, 0, tzinfo=ZoneInfo(TZ))
    assert (tierra.min_price, tierra.max_price) == (Decimal(80), Decimal(90))
    assert tierra.venue_name == "האזור" and tierra.city == "תל אביב-יפו"
    spirin = next(e for e in events if e and e.title == "Dmitry Spirin")
    assert (spirin.min_price, spirin.max_price) == (Decimal(249), Decimal(599))  # "249-599 ש"ח"

    haezor.add_page(tierra, (FIXTURES / "haezor" / "event.html").read_text(encoding="utf-8"))
    assert tierra.images == ["https://haezor.com/wp-content/uploads/TIERRA-LATINA.png"]
    assert tierra.source_genres == ["מוזיקה לטינית"]
    assert (tierra.extra or {})["purchase_url"].startswith("https://secure.cardcom.solutions/")
    assert "סלסה" in (tierra.description or "")

    # No year on the page: a January show read in October is next year's.
    assert haezor.show_day("5 בינואר יום שני", date(2026, 10, 25)) == date(2027, 1, 5)
    assert haezor.show_day("24 באוקטובר שבת", date(2026, 10, 25)) == date(2026, 10, 24)


def test_levontin7_home_and_show_page() -> None:
    cfg = levontin7.Levontin7Settings.model_validate(Settings().sources["levontin7"])
    cards = levontin7.parse_listing((FIXTURES / "levontin7" / "home.html").read_text(encoding="utf-8"))
    assert len(cards) == 65 and len({c["id"] for c in cards}) == 65  # each show once, though repeated on the page
    rage = levontin7.parse_card(next(c for c in cards if "rage-against" in c["url"]), cfg, TZ)
    assert rage is not None
    # The plugin's Unix time is the Israeli clock: the 20:00 show is at 20:00 here, not 22:00.
    assert rage.start_at == datetime(2026, 10, 30, 20, 0, tzinfo=ZoneInfo(TZ))
    assert rage.images and not rage.images[0].endswith("x437.jpg")  # the full image, not the thumbnail

    levontin7.add_page(rage, (FIXTURES / "levontin7" / "event.html").read_text(encoding="utf-8"))
    assert rage.title == "Rage Against The Machine Tribute // 30.10"
    assert rage.min_price == Decimal(70)
    assert (rage.extra or {})["purchase_url"] == "https://www.eventer.co.il/k212f"
    assert "סופרגרופ" in (rage.description or "")
