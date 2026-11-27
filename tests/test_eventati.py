"""Eventati: the events widget, the event sitemap and one Hebrew event page, trimmed real answers (27.11.2026)."""

from __future__ import annotations

from pathlib import Path

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.eventati import list_slugs, parse_event, sitemap_slugs

FIXTURES = Path(__file__).parent / "fixtures" / "eventati"
TZ = "Asia/Jerusalem"
URL = "https://www.eventati.org/he/event-details/anhyar"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_list_slugs() -> None:
    slugs = list_slugs(_read("allevents.html"))
    assert len(slugs) == 10 and "anhyar" in slugs and "wynk-ya-baba-nawyl" in slugs


def test_sitemap_keeps_recent_pages_only() -> None:
    slugs = sitemap_slugs(_read("sitemap.xml"), "2026-01-01")
    assert "anhyar" in slugs and "mhmd-n-mt-fy-sfya-2" in slugs  # an upcoming show the widget does not list
    assert "wd-tnws" not in slugs  # changed 16.12.2025: a past event


def test_event_page() -> None:
    event = parse_event(_read("event.html"), URL, TZ)
    assert event is not None
    assert event.title == "קריסה"
    assert event.start_at is not None and event.start_at.isoformat() == "2026-12-14T19:00:00+02:00"
    assert event.end_at is None  # the site hides its end
    assert event.venue_name == "תיאטרון אלחנין" and event.city == "נצרת" and event.address == "נצרת"  # the pin's town
    assert event.latitude == 32.6898301 and event.longitude == 35.2981048
    assert [(t.name, t.price) for t in event.tickets] == [("כרטיס יחיד", 30)]
    assert event.min_price == 30 and event.extra["purchase_url"] == URL
    assert event.images and event.images[0].startswith("https://static.wixstatic.com/media/")


def test_cancelled_and_undated_pages_are_skipped() -> None:
    page = _read("event.html")
    assert parse_event(page.replace('"status": 0', '"status": 3'), URL, TZ) is None
    assert parse_event(page.replace('"scheduleTbd": false', '"scheduleTbd": true'), URL, TZ) is None
    assert parse_event("<html>Checking Your Request</html>", URL, TZ) is None


def test_enrichment_places_it_in_nazareth() -> None:
    event = parse_event(_read("event.html"), URL, TZ)
    assert event is not None
    enriched = Enricher(Settings().enrichment, TZ).enrich(event)
    assert enriched is not None and enriched.city == "נצרת" and enriched.genres
