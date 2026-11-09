"""Makore: category page JSON-LD to ScrapedEvent."""

from __future__ import annotations

import json
from datetime import UTC, date
from decimal import Decimal
from pathlib import Path

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.makore import listing_total, parse_item, parse_listing

TZ = "Asia/Jerusalem"
PAGE = (Path(__file__).parent / "fixtures/makore/nightlife.html").read_text("utf-8")


def test_listing_items_and_category_total() -> None:
    assert len(parse_listing(PAGE)) == 4
    assert listing_total(PAGE) == 239


def test_item_venue_coordinates_and_box_office() -> None:
    items = parse_listing(PAGE)
    e = parse_item(items[1], "חיי-לילה", TZ)
    assert e is not None and e.source_event_id == "32f2147b" and e.title == "רייב קריאה"
    assert e.start_at is not None and e.start_at.astimezone(UTC).isoformat() == "2026-10-21T17:00:00+00:00"
    assert (e.venue_name, e.address, e.city) == (
        "בית רדיקל",
        "התחייה 27",
        "תל אביב-יפו",
    )  # city suffix dropped
    assert e.latitude == 32.0487236 and e.country is None
    assert e.min_price == Decimal(40) and e.extra["purchase_url"] == "https://radical.org.il/product/reading-rave/"
    assert e.event_types == ["חיי-לילה"] and e.images[0].startswith("https://images.makore.co.il/")
    assert parse_item(items[2], "חיי-לילה", TZ).end_at is not None  # type: ignore[union-attr]


def test_midnight_means_the_site_has_the_date_only() -> None:
    item = parse_listing(PAGE)[1]
    guess = parse_item(item | {"startDate": "2026-11-20T22:00:00Z"}, "חיי-לילה", TZ)  # 00:00 in Israel
    assert guess is not None and guess.time_known is False
    real = parse_item(item | {"startDate": "2026-11-20T20:00:00Z"}, "חיי-לילה", TZ)
    assert real is not None and real.time_known is True
    # Enrichment keeps it, on the right day: it reads the hour off start_at as given.
    settings = Settings()
    enricher = Enricher(settings.enrichment, settings.timezone)
    enricher._categorize_time(guess)
    assert guess.time_known is False and guess.local_date == date(2026, 11, 21)
    enricher._categorize_time(real)
    assert real.time_known is True and real.local_date == date(2026, 11, 20)


def test_place_without_address_and_cancelled_event() -> None:
    items = parse_listing(PAGE)
    e = parse_item(items[0], "חיי-לילה", TZ)
    assert e is not None and e.source_event_id == "25f6fc42" and e.city is None and e.min_price is None
    assert parse_item(items[3], "חיי-לילה", TZ) is None  # EventCancelled, URL without a slug


def test_series_page_lists_its_dates() -> None:
    from partylinks.scrapers.makore import series_links

    item = {"@type": "ListItem", "position": "1", "url": "https://www.makore.co.il/event/flute--c6064335"}
    page = (
        '<script type="application/ld+json">'
        + json.dumps({"@type": "ItemList", "itemListElement": [item, item | {"position": "2"}]})
        + "</script>"
    )
    assert series_links(page) == ["https://www.makore.co.il/event/flute--c6064335"]
    assert series_links("<html></html>") == []
