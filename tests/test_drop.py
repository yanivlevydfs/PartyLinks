"""Drop: sitemap listing and event-page JSON-LD to ScrapedEvent."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from partylinks.models import TicketKind
from partylinks.scrapers.drop import parse_event, upcoming_urls

TZ = "Asia/Jerusalem"
FIXTURES = Path(__file__).parent / "fixtures/drop"


def test_sitemap_keeps_event_pages_dated_from_now_on() -> None:
    sitemap = (FIXTURES / "sitemap.xml").read_text("utf-8")
    assert upcoming_urls(sitemap, datetime(2026, 10, 20, tzinfo=UTC)) == [
        "https://tdrop.net/events/edb2b19a0dd34733",
        "https://tdrop.net/events/38bcef53e03547ca",
    ]


def test_event_page_without_trusting_the_zero_price() -> None:
    url = "https://tdrop.net/events/edb2b19a0dd34733"
    e = parse_event((FIXTURES / "event_edb2b19a0dd34733.html").read_text("utf-8"), url, TZ)
    assert e is not None and e.source_event_id == "edb2b19a0dd34733" and e.title == "🦩TRANCELA⚔️"
    assert e.start_at is not None and e.start_at.isoformat() == "2026-11-28T10:00:00+00:00"
    assert e.venue_name == "שטח פרטי - מרכז הארץ" and e.address is None and e.country is None
    assert e.organizer and "Dor’s Tribe" in e.organizer
    assert e.description and e.description.startswith("🦩⚔️ טראנסלה")  # "[HTMLDESC]" prefix dropped
    assert [t.name for t in e.tickets] == ["⚔️כרטיסים ראשונים מוזלים לקהילה וחברים🦩"]
    # Every offer reads price "0": unknown, not free.
    assert e.tickets[0].price is None and e.tickets[0].kind is TicketKind.UNKNOWN and e.min_price is None
    assert e.images[0].startswith("https://aykrrozfnlzuvrqovvlx.supabase.co/storage/")
