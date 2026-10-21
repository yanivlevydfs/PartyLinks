"""Megatickets: listing links and JSON-LD event parsing on trimmed real pages (no network)."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path

from partylinks.config import HttpSettings
from partylinks.models import TierStatus
from partylinks.scrapers.megatickets import MegaticketsScraper, MegaticketsSettings, parse_event

FIXTURES = Path(__file__).parent / "fixtures" / "megatickets"
URL = "https://www.megatickets.co.il/events/markus-schulz-the-place-29-10"


def test_parse_event() -> None:
    event = parse_event((FIXTURES / "event.html").read_text(encoding="utf-8"), URL, "Asia/Jerusalem")

    assert event.source == "megatickets"
    assert event.source_event_id == "markus-schulz-the-place-29-10"
    assert event.title == "Markus Schulz - The Place"
    assert event.start_at.isoformat() == "2026-10-29T23:00:00+02:00"
    assert event.end_at.isoformat() == "2026-10-30T05:00:00+02:00"
    assert event.venue_name == "The Place"
    assert event.address == "קיבוץ גלויות 52, תל אביב"
    assert event.city == "תל אביב"
    assert event.min_age == 23
    assert event.artists[0] == "Markus Schulz" and len(event.artists) == 5
    # The /api/og share card is dropped; real artwork comes first.
    assert event.image_url and event.image_url.endswith("hero-1791407963.jpg")
    assert not any("/api/" in i for i in event.images)

    tiers = {t.name: t for t in event.tickets}
    assert len(tiers) == 4
    assert tiers["מחיר השקה"].status is TierStatus.SOLD_OUT
    assert tiers["מכירה מוקדמת"].price == Decimal(179) and tiers["מכירה מוקדמת"].is_active
    assert (event.min_price, event.max_price) == (Decimal(179), Decimal(299))
    assert not event.is_sold_out


def test_listing_slugs() -> None:
    settings = MegaticketsSettings(
        cron="0 * * * *", site_url="https://www.megatickets.co.il", list_path="/events", event_page_path="/events/{slug}"
    )
    scraper = MegaticketsScraper(settings, HttpSettings(), "Asia/Jerusalem")
    listing = (FIXTURES / "listing.html").read_text(encoding="utf-8")

    async def fake_get_text(url: str, **_: object) -> str:
        assert url == "https://www.megatickets.co.il/events"
        return listing

    scraper.get_text = fake_get_text  # type: ignore[method-assign]
    assert asyncio.run(scraper._list_slugs()) == [
        "ground-before-the-election-day-the-place-26-10",
        "harmonya-halloween-haoman-17-31-10",
        "markus-schulz-the-place-29-10",
    ]
