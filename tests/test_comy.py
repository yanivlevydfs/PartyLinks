"""Comy parser test against a trimmed real show page (3 of its performances) and home links."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from partylinks.scrapers import comy

FIXTURES = Path(__file__).parent / "fixtures" / "comy"
TZ = "Asia/Jerusalem"
PAGE = "https://comy.co.il/event/adir-miller/"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_comy_listing_pages() -> None:
    assert comy.listing_pages(_read("home.html"), "https://comy.co.il/event/") == [
        PAGE,
        "https://comy.co.il/event/reshef-levi/",
    ]


def test_comy_show_performances() -> None:
    sold_out, herzliya, amsterdam = comy.parse_show(_read("event.html"), PAGE, TZ, date(2026, 10, 20))

    assert herzliya.title == "אדיר מילר" and herzliya.source_genres == ["סטנדאפ"]
    assert herzliya.start_at == datetime(2026, 11, 20, 22, 30, tzinfo=ZoneInfo(TZ))
    assert herzliya.start_at is not None
    assert herzliya.start_at.utcoffset() is not None
    assert herzliya.venue_name == "היכל אומנויות הבמה הרצליה"
    assert str(herzliya.url) == "https://tickets.comy.co.il/booking/features/825?prsntId=55364"
    assert herzliya.source_event_id == "adir-miller:202611202230" and herzliya.extra["presentation_id"] == "55364"
    assert herzliya.min_price == herzliya.max_price == Decimal(125)
    assert herzliya.image_url and herzliya.image_url.endswith("Adir-Miller_350.jpg")
    assert herzliya.description and herzliya.description.startswith("במופע סטנדאפ")
    assert not herzliya.is_sold_out

    # Sold-out rows link "#": they point at the show page.
    assert sold_out.is_sold_out and str(sold_out.url) == PAGE
    assert sold_out.venue_name == "היכל התרבות ראשון לציון"
    # dd.mm before the run's first date rolls into the next year.
    assert amsterdam.start_at == datetime(2027, 1, 9, 20, 30, tzinfo=ZoneInfo(TZ))
    assert amsterdam.venue_name == "אמסטרדם" and "weeztix" in str(amsterdam.url)
