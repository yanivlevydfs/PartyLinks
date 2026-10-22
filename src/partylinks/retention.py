"""Retention: purge events that are over (with tickets and artwork), old run history, and
give the freed space back to the OS."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, and_, delete, false, not_, or_, select

from partylinks.config import RetentionSettings
from partylinks.db import Database, EventRow, ScrapeRunRow, delete_events
from partylinks.media import MediaStore

log = logging.getLogger(__name__)


def retention_cutoff(settings: RetentionSettings, timezone: str, now: datetime | None = None) -> datetime:
    """Local midnight ``keep_days_back`` days ago (0 = today onwards)."""
    tz = ZoneInfo(timezone)
    today = (now or datetime.now(tz)).astimezone(tz).date()
    return datetime.combine(today - timedelta(days=settings.keep_days_back), time.min, tzinfo=tz)


# --------------------------------------------------------------------------- the one rule
# An event is current when it starts on/after the cutoff (today, local), or when it
# started within the last `in_progress_max_hours` and its end is still ahead (a party
# that began last night and is still going). Everything else is over: never imported,
# never shown, and purged. Used by import (service), display (db.upcoming_filter) and
# purge (below) so the three can never disagree.


def is_current(
    start: datetime | None, end: datetime | None, settings: RetentionSettings, timezone: str,
    now: datetime | None = None,
) -> bool:  # fmt: skip
    if start is None:
        return True  # undated: kept until the source stops listing it (see purge_expired)
    now = now or datetime.now(UTC)
    if start >= retention_cutoff(settings, timezone, now):
        return True
    return (
        settings.keep_in_progress
        and end is not None
        and end > now
        and start >= now - timedelta(hours=settings.in_progress_max_hours)
    )


def current_clause(settings: RetentionSettings, timezone: str, now: datetime | None = None) -> ColumnElement[bool]:
    """SQL form of :func:`is_current` for dated events."""
    now = now or datetime.now(UTC)
    upcoming = EventRow.start_at >= retention_cutoff(settings, timezone, now)
    if not settings.keep_in_progress:
        return upcoming
    # end_at IS NOT NULL is explicit: without it a NULL end makes the whole clause NULL
    # (SQL three-valued logic), and NOT(NULL) would keep undated-end events forever.
    in_progress = and_(
        EventRow.end_at.is_not(None),
        EventRow.end_at > now,
        EventRow.start_at >= now - timedelta(hours=settings.in_progress_max_hours),
    )
    return or_(upcoming, in_progress)


def purge_expired(
    db: Database, media: MediaStore, settings: RetentionSettings, timezone: str, drop_other_cities: bool = False
) -> int:
    """Delete events that are over (see :func:`is_current`) with their tickets and artwork.

    The start date decides, not a far end date: some listings carry an end weeks later
    (e.g. a recurring "season" page) and must not linger as if they were upcoming.
    """
    cutoff = retention_cutoff(settings, timezone)
    with db.session() as session:
        expired = session.scalars(
            select(EventRow.id).where(
                or_(
                    and_(EventRow.start_at.is_not(None), not_(current_clause(settings, timezone))),
                    # Undated events disappear once the source stops listing them.
                    and_(EventRow.start_at.is_(None), EventRow.last_seen_at < cutoff),
                    # Out of the target city (only when the config focuses on one city).
                    EventRow.in_target_city.is_(False) if drop_other_cities else false(),
                )
            )
        ).all()
        files = delete_events(session, expired)
        runs = session.execute(
            delete(ScrapeRunRow).where(
                ScrapeRunRow.started_at < cutoff - timedelta(days=settings.keep_runs_days),
                ScrapeRunRow.status != "running",
            )
        ).rowcount
    removed_files = media.delete_files(files)
    log.info(
        "retention: removed %d events, %d artwork files and %d runs older than %s",
        len(expired), removed_files, runs, cutoff,
    )  # fmt: skip
    if settings.reclaim_space and (expired or runs):
        db.reclaim_space()
    return len(expired)
