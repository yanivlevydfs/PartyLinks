"""HaMenagen parser test against three items of the real app API (October 2026)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from partylinks.scrapers import hamenagen

FIXTURE = Path(__file__).parent / "fixtures" / "hamenagen" / "events.json"
ITEMS = json.loads(FIXTURE.read_text(encoding="utf-8"))["items"]
EVENTS_URL = "https://hamenagen.net/events/"
TZ = "Asia/Jerusalem"


def test_hamenagen_items() -> None:
    events = [hamenagen.parse_item(item, EVENTS_URL, TZ) for item in ITEMS]
    shir, carlebach, friends = events
    assert (shir.source_event_id, shir.venue_name, shir.city) == (
        "d47ce777-fbc2-4a92-8ea4-465ce15c85a7",
        "בנייני האומה- אולם אוסשקין",
        "ירושלים",
    )
    assert shir.start_at == datetime(2026, 10, 24, 21, 0, tzinfo=ZoneInfo(TZ))
    assert str(shir.url) == shir.extra["purchase_url"] == "https://tickchak.co.il/111977"
    assert shir.image_url.endswith("-1024x683.webp")  # the full flyer, not the thumbnail
    assert shir.artists == ["הרב שלמה קרליבך"] and shir.artist_profiles[0].profile_url.startswith(
        "https://hamenagen.net/singers/"
    )
    # Several artists; a location without a comma is the venue only (no city).
    assert (carlebach.artists, carlebach.venue_name, carlebach.city) == (
        ["דוד בן ארזה", "יצחק בן ארזה"],
        "היכל תרבות רמות",
        None,
    )
    assert friends.start_at == datetime(2026, 11, 8, 20, 30, tzinfo=ZoneInfo(TZ))


def test_hamenagen_fallbacks() -> None:
    bare = {"id": "x1", "title": "ערב ניגונים", "date": "2026-12-01", "timeLabel": "", "singerName": "א, ב"}
    event = hamenagen.parse_item(bare, EVENTS_URL, TZ)
    assert (str(event.url), event.extra, event.artists) == (EVENTS_URL, {}, ["א", "ב"])
    assert event.start_at == datetime(2026, 12, 1, tzinfo=ZoneInfo(TZ))  # date only
    assert hamenagen.parse_item({**bare, "date": "soon"}, EVENTS_URL, TZ) is None
