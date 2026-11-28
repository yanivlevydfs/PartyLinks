"""Curated: SPOTX's own list (config sources.curated.events), each entry one event per day or one over its days."""

from __future__ import annotations

from datetime import time

from partylinks.config import get_settings
from partylinks.scrapers.curated import CuratedEvent, CuratedSettings, events_of

TZ = "Asia/Jerusalem"


def _entry(**fields: object) -> CuratedEvent:
    base: dict[str, object] = {"id": "x", "title": "שוק", "dates": ["2026-12-16..2026-12-20"], "venue": "חצר",
                               "city": "נצרת", "description": "d", "source_url": "https://example.com/a"}  # fmt: skip
    return CuratedEvent.model_validate(base | fields)


def test_a_range_is_one_event_over_its_days() -> None:
    (event,) = events_of(_entry(free=True), TZ)
    assert event.start_at is not None and event.start_at.isoformat() == "2026-12-16T00:00:00+02:00"
    assert event.end_at is not None and event.end_at.isoformat() == "2026-12-21T00:00:00+02:00"  # the end of the 20th
    assert not event.time_known and event.tickets[0].is_free and event.min_price == 0
    assert str(event.url) == "https://example.com/a#x" and event.extra["purchase_url"] == "https://example.com/a"


def test_daily_is_one_event_a_day_with_its_hours() -> None:
    entry = _entry(dates=["2026-12-02..2026-12-03", "2026-12-10"], daily=True, start=time(17), end=time(22),
                   prices=[{"name": "כניסה", "price": 50}, {"name": "ילדים", "price": 10}])  # fmt: skip
    events = events_of(entry, TZ)
    assert [e.source_event_id for e in events] == ["x:2026-12-02", "x:2026-12-03", "x:2026-12-10"]
    first = events[0]
    assert first.start_at is not None and first.start_at.isoformat() == "2026-12-02T17:00:00+02:00"
    assert first.end_at is not None and first.end_at.isoformat() == "2026-12-02T22:00:00+02:00"
    assert (first.min_price, first.max_price) == (10, 50) and first.time_known


def test_the_configured_list_is_valid() -> None:
    settings = get_settings().sources["curated"]
    curated = CuratedSettings.model_validate(settings)
    ids = [e.id for e in curated.events]
    assert len(ids) == len(set(ids)) and curated.may_be_empty
    for entry in curated.events:
        assert events_of(entry, TZ), entry.id
