"""Muzi: sitemap listing and event-page parsing on trimmed real pages (no network)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from partylinks.scrapers.muzi import event_urls, parse_event, sitemap_urls

FIXTURES = Path(__file__).parent / "fixtures" / "muzi"
TZ = "Asia/Jerusalem"
URL = "https://muzi.co.il/event/jazz-eilat/"


def _page(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_sitemaps() -> None:
    index = "<sitemap><loc>https://muzi.co.il/post-sitemap.xml</loc></sitemap><sitemap><loc>https://muzi.co.il/event-sitemap2.xml</loc></sitemap>"
    assert sitemap_urls(index)[1] == "https://muzi.co.il/event-sitemap2.xml"
    sitemap = (
        "<url><loc>https://muzi.co.il/event/</loc></url>"
        "<url><loc>https://muzi.co.il/event/%d7%a1%d7%95%d7%9c-2/</loc></url>"
        "<url><loc>https://muzi.co.il/event/a&amp;b/</loc></url>"
    )
    assert event_urls(sitemap, "/event/") == [
        "https://muzi.co.il/event/%d7%a1%d7%95%d7%9c-2/",
        "https://muzi.co.il/event/a&b/",
    ]


def test_paid_event() -> None:
    event = parse_event(_page("event_paid.html"), URL, TZ, today=date(2026, 10, 23))
    assert event is not None
    assert event.source == "muzi"
    assert event.source_event_id == "109458"
    assert event.title.startswith("פסטיבל ג’אז בים האדום אילת")
    assert event.start_at is not None and event.start_at.isoformat() == "2026-11-14T14:15:00+02:00"
    assert (event.venue_name, event.city, event.country) == ("PORT ARENA", "אילת", None)
    assert event.artists == ["בלקן ביט בוקס"]
    assert event.source_genres == ["jazz"]
    assert event.min_price == event.max_price == Decimal(229)
    assert event.image_url == "https://muzi.co.il/wp-content/uploads/2025/02/67b1a0b24ed57.webp"
    assert event.extra == {
        "purchase_url": "https://www.ticketmaster.co.il/performance/MEP29/001/ALL/iw",
        "doors_open": "13:30",
    }


def test_free_and_range_prices() -> None:
    free = parse_event(_page("event_free.html"), URL, TZ)
    assert free is not None and free.min_price == Decimal(0) and free.tickets[0].is_free
    assert free.start_at is not None and free.start_at.isoformat() == "2026-10-28T12:30:00+02:00"
    ranged = parse_event(_page("event_range.html"), URL, TZ)
    assert ranged is not None and (ranged.min_price, ranged.max_price) == (Decimal(50), Decimal(60))
    assert ranged.source_genres == ["electronic"]


def test_past_event_skipped() -> None:
    assert parse_event(_page("event_free.html"), URL, TZ, today=date(2026, 10, 29)) is None


def test_event_block_with_raw_newlines() -> None:
    """The event JSON-LD keeps raw newlines in its description (invalid strict JSON)."""
    event = parse_event(_page("event_raw_newlines.html"), URL, TZ)
    assert event is not None and event.title == "Bayz Rooftop The Last Dance"
    assert event.start_at is not None and event.start_at.isoformat() == "2026-10-24T14:00:00+03:00"
    assert event.extra["purchase_url"].startswith("https://go-out.co/event/1792588673056")
    assert event.artists == ["GENISH"]


def test_unescaped_backslash_and_quotes_in_description() -> None:
    page = _page("event_raw_newlines.html").replace('"description": "Genish |', r'"description": "Genish \ "Orkey" |')
    event = parse_event(page, URL, TZ)
    assert event is not None and event.description and event.description.startswith(r'Genish \ "Orkey"')


def test_unescaped_quotes_outside_the_description() -> None:
    """A place written with a plain ASCII gershayim ("ויצ"ו חיפה") broke the whole block: the show was lost."""
    page = _page("event_raw_newlines.html").replace('"@type": "Place",', '"@type": "Place", "alternateName": "ויצ"ו חיפה",')
    assert '"ויצ"ו' in page
    event = parse_event(page, URL, TZ)
    assert event is not None and event.title == "Bayz Rooftop The Last Dance"
