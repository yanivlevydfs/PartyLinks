"""Tzkrti: one real API item (trimmed), times in UTC, categories, place and the courses filter."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from partylinks.config import get_settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.tzkrti import TzkrtiSettings, parse_event

TZ = "Asia/Jerusalem"
SETTINGS = TzkrtiSettings(cron="27 7,19 * * *")
PAGE = Path(__file__).parent / "fixtures" / "tzkrti" / "page.json"
ITEM = json.loads(PAGE.read_text(encoding="utf-8"))["events"]["items"][0]


def test_parse_event() -> None:
    event = parse_event(ITEM, SETTINGS, TZ)
    assert event is not None
    assert (event.source_event_id, str(event.url)) == ("4618", "https://tzkrti.com/en/checkout/4618")
    assert event.title == "تريو جبران - 20 ربيعاً"
    # from_date "2026-11-21 18:00" is UTC: the 20:00 show (from_time_only "08:00 PM").
    assert event.start_at is not None
    assert event.start_at.isoformat() == "2026-11-21T20:00:00+02:00"
    assert event.end_at is not None and event.end_at.isoformat() == "2026-11-21T22:00:00+02:00"
    assert (event.venue_name, event.city) == ("مسرح الكونجرسيم", "Haifa")
    assert event.address == "Kdoshei Jassy St 2, Haifa, Israel"
    assert event.latitude is not None and event.longitude is not None
    assert (round(event.latitude, 3), round(event.longitude, 3)) == (32.791, 34.965)
    assert event.source_genres == ["music", "موسيقى"]
    assert event.min_price == Decimal(190)
    assert event.images == ["https://tzkrti.com/storage/images/JKPqpiQdV4WPlgo99OfCj9SmsGQjZ0nySsB7j391.jpg"]
    assert event.extra["purchase_url"] == "https://tzkrti.com/en/checkout/4618"
    enriched = Enricher(get_settings().enrichment, TZ).enrich(event)
    assert enriched is not None and enriched.genres and enriched.city == "חיפה"


def test_courses_and_past_are_not_events_private_is() -> None:
    course = {**ITEM, "category": {"name": {"en": "Courses", "ar": "دورات"}}}
    assert parse_event(course, SETTINGS, TZ) is None
    assert parse_event({**ITEM, "is_past": 1}, SETTINGS, TZ) is None
    assert parse_event({**ITEM, "private": 1, "unlisted": 1}, SETTINGS, TZ) is not None
    assert parse_event({**ITEM, "from_date": None}, SETTINGS, TZ) is None


def test_checkout_page_event_and_probe_filter() -> None:
    import html as html_lib

    item = {**ITEM, "published": 1, "cancelled": 0, "country_iso": "IL", "sale_status": "On Sale", "unlisted": 1}
    page = f'<checkout-landing\n :event="{html_lib.escape(json.dumps(item))}" :other="1">'
    from partylinks.scrapers.tzkrti import parse_embedded, probe_wanted

    found = parse_embedded(page)
    assert found is not None and found["id"] == 4618 and probe_wanted(found, SETTINGS)
    assert not probe_wanted({**found, "published": 0}, SETTINGS)
    assert not probe_wanted({**found, "sale_status": "On Hold"}, SETTINGS)
    assert not probe_wanted({**found, "country_iso": "JO"}, SETTINGS)
    assert not probe_wanted({**found, "cancelled": 1}, SETTINGS)
