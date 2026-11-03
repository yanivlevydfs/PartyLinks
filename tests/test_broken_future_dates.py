"""A start years ahead is a source's broken date (2038-01-19 03:14:07, a 2025 festival sent as 2032): never
imported, never shown, purged - by the same retention rule as past events."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from partylinks.config import RetentionSettings, Settings
from partylinks.db import Database, EventRow, upsert_events
from partylinks.media import MediaStore
from partylinks.models import ScrapedEvent
from partylinks.retention import is_current, purge_expired

TZ = "Asia/Jerusalem"
RULE = RetentionSettings()
CLOCK_END = datetime(2038, 1, 19, 3, 14, 7, tzinfo=UTC)


def test_is_current_refuses_starts_past_the_horizon() -> None:
    now = datetime(2026, 11, 3, 8, tzinfo=UTC)
    assert not is_current(CLOCK_END, None, RULE, TZ, now=now)
    assert not is_current(now + timedelta(days=RULE.max_future_days + 1), None, RULE, TZ, now=now)
    assert is_current(now + timedelta(days=540), None, RULE, TZ, now=now)  # a tour 1.5 years ahead is real
    assert is_current(now + timedelta(days=3), None, RULE, TZ, now=now)


def _event(event_id: str, start: datetime) -> ScrapedEvent:
    return ScrapedEvent(
        source="test", source_event_id=event_id, url=f"https://example.com/{event_id}", title=event_id, start_at=start
    )


def test_purge_removes_stored_broken_dates_and_keeps_real_ones() -> None:
    settings = Settings(scheduler={"enabled": False}, media={"download": False, "storage": "database"})
    db = Database(settings.database.url)
    db.create_schema()
    now = datetime.now(UTC)
    with db.session() as session:
        upsert_events(session, [_event("clock-end", CLOCK_END), _event("next-year", now + timedelta(days=400))])

    purge_expired(db, MediaStore(settings.media, db), settings.retention, settings.timezone)

    with db.session() as session:
        assert {row.id for row in session.query(EventRow)} == {"test:next-year"}
