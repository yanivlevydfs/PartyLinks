"""Drop: sitemap listing and event-page JSON-LD to ScrapedEvent."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from partylinks.models import TicketKind, TierStatus
from partylinks.scrapers.drop import _abroad, _api_tier, parse_event, upcoming_urls, with_api_tickets

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


def test_api_tiers_give_prices_and_sold_out_rounds() -> None:
    url = "https://tdrop.net/events/edb2b19a0dd34733"
    page = parse_event((FIXTURES / "event_edb2b19a0dd34733.html").read_text("utf-8"), url, TZ)
    assert page is not None
    api = json.loads((FIXTURES / "api_3fecd685ed0449f5.json").read_text("utf-8"))
    e = with_api_tickets(page, api)
    assert [(t.name, t.price, t.status) for t in e.tickets] == [
        ("Sof Heznek 26.11", Decimal(70), TierStatus.SOLD_OUT),
        ("Sof Heznek סבב ב׳", Decimal(85), TierStatus.ON_SALE),
    ]
    assert e.tickets[1].remaining == 25 and not e.is_sold_out
    assert with_api_tickets(page, {}).tickets == page.tickets  # API down or empty: JSON-LD tiers kept
    extras = with_api_tickets(page, {"categories": [{"name": "Trance"}], "minAge": 26})
    assert extras.source_genres == ["trance"]
    assert extras.min_age == 26


def test_api_discount_counts_only_below_price() -> None:
    tier = {"name": "x", "price": 100, "discountedPrice": 80, "status": "current"}
    assert _api_tier(tier).price == 80 and _api_tier(tier).original_price == 100
    assert _api_tier({**tier, "discountedPrice": 0}).price == 100


def test_a_place_named_with_a_foreign_country_last_is_abroad() -> None:
    assert _abroad("Hilltop - Goa - India") == "IN"
    assert _abroad("Pushkar , rajastan , India") == "IN"
    assert _abroad("החרש 8, ישראל") is None
    assert _abroad("ירדן") is None  # one word: a place, not a country
