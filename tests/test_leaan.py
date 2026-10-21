"""Leaan parser test against trimmed real pages (homepage search index, one event page, sitemap)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from partylinks.scrapers import leaan

FIXTURES = Path(__file__).parent / "fixtures" / "leaan"
TZ = "Asia/Jerusalem"
URL = "https://www.leaan.co.il/events/{slug}/{event_id}"
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_leaan_listing_events() -> None:
    raws = leaan.listing_events(_read("home.html"))
    parsed = {str(r["id"]): leaan.parse_event(r, URL, TZ, NOW) for r in raws}
    # Season ticket (6803), zero-priced refund (7429) and an already-started run (6479) are skipped.
    assert {k for k, v in parsed.items() if v is None} == {"6803", "7429", "6479"}

    banai = parsed["7694"]
    assert banai is not None and banai.title == "ליאת בנאי"
    assert str(banai.url).endswith("/7694")
    assert banai.start_at == datetime(2026, 11, 11, 21, 30, tzinfo=ZoneInfo(TZ))
    assert (banai.venue_name, banai.address, banai.city) == ("מועדון הגריי", "דם המכבים 69", "מודיעין")
    assert banai.source_genres == ["מוזיקה"]
    assert banai.min_price == banai.max_price == Decimal(220) and not banai.tickets
    assert banai.image_url and len(banai.images) == 3
    assert banai.description and "ליאת בנאי" in banai.description

    standup = parsed["6503"]
    assert standup is not None and standup.min_age == 18 and standup.extra["labels"] == ["18+"]

    match = parsed["7951"]
    assert match is not None and match.min_price == Decimal(75) and match.max_price is None
    assert match.extra["purchase_url"] == "https://tickets.leaan.net/event/--cbxrhu" and match.extra["sport"]


def test_leaan_event_page_tiers() -> None:
    event = leaan.parse_event(leaan.event_page_data(_read("event_7957.html")), URL, TZ, NOW)
    assert event is not None and event.source_event_id == "7957"
    assert event.city == "תל אביב" and event.venue_name == "היכל קבוצת שלמה"
    assert len(event.tickets) == 10 and event.tickets[0].name == "נוער/חייל - לא מסומן"
    assert (event.min_price, event.max_price) == (Decimal(90), Decimal(400))


def test_leaan_sitemap_ids() -> None:
    assert leaan.sitemap_event_ids(_read("sitemap.xml"), "/events/{slug}/{event_id}") == ["7957", "7694"]
