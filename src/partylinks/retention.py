"""Retention: purge events that are over (with tickets and artwork) and old run history
(PostgreSQL's autovacuum reuses the freed space)."""

from __future__ import annotations

import logging
import typing
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, and_, delete, false, not_, or_, select
from sqlalchemy.engine import CursorResult

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
# An event is current when it starts on/after the cutoff (today, local) and has not ended yet
# (a morning show that ended at 10:00 is gone by the evening), or when it
# started within the last `in_progress_max_hours` and its end is still ahead (a party
# that began last night and is still going). An event with no end time ends
# `assumed_duration_hours` after its start (nobody arrives three hours late); an all-day listing
# (no hour: `time_known` false, or a 00:00 start) ends at midnight. Everything else is over: never
# imported, never shown, and purged. Used by import (service), display (db.upcoming_filter) and
# purge (below) so the three can never disagree.


def _all_day(start: datetime, time_known: bool, timezone: str) -> bool:
    return not time_known or start.astimezone(ZoneInfo(timezone)).time() == time.min


def is_current(
    start: datetime | None, end: datetime | None, settings: RetentionSettings, timezone: str,
    now: datetime | None = None, time_known: bool = True,
) -> bool:  # fmt: skip
    if start is None:
        return True  # undated: kept until the source stops listing it (see purge_expired)
    now = now or datetime.now(UTC)
    today = start >= retention_cutoff(settings, timezone, now)
    if settings.keep_days_back > 0:
        return today or bool(end and _in_progress(start, end, settings, now))
    if end is None:
        if _all_day(start, time_known, timezone):
            return today
        return start + timedelta(hours=settings.assumed_duration_hours) > now
    return (today and end > now) or _in_progress(start, end, settings, now)


def _in_progress(start: datetime, end: datetime, settings: RetentionSettings, now: datetime) -> bool:
    return settings.keep_in_progress and end > now and start >= now - timedelta(hours=settings.in_progress_max_hours)


def current_clause(settings: RetentionSettings, timezone: str, now: datetime | None = None) -> ColumnElement[bool]:
    """SQL form of :func:`is_current` for dated events."""
    now = now or datetime.now(UTC)
    today = EventRow.start_at >= retention_cutoff(settings, timezone, now)
    # end_at IS NOT NULL is explicit: without it a NULL end makes the whole clause NULL
    # (SQL three-valued logic), and NOT(NULL) would keep undated-end events forever.
    in_progress = (
        and_(
            EventRow.end_at.is_not(None),
            EventRow.end_at > now,
            EventRow.start_at >= now - timedelta(hours=settings.in_progress_max_hours),
        )
        if settings.keep_in_progress
        else false()
    )
    if settings.keep_days_back > 0:
        return or_(today, in_progress)
    # All-day listing: no hour given, or a 00:00 start. Only a start between midnight and the assumed duration
    # ago needs telling apart, and there a 00:00 start is today's midnight itself (no time zone math in SQL).
    cutoff = retention_cutoff(settings, timezone, now)
    all_day = or_(EventRow.time_known.is_(False), EventRow.start_at == cutoff)
    no_end = and_(
        EventRow.end_at.is_(None),
        or_(
            and_(all_day, today),
            and_(not_(all_day), EventRow.start_at > now - timedelta(hours=settings.assumed_duration_hours)),
        ),
    )
    with_end = and_(EventRow.end_at.is_not(None), today, EventRow.end_at > now)
    return or_(no_end, with_end, in_progress)


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
        result = session.execute(
            delete(ScrapeRunRow).where(
                ScrapeRunRow.started_at < cutoff - timedelta(days=settings.keep_runs_days),
                ScrapeRunRow.status != "running",
            )
        )
        runs = typing.cast("CursorResult[Any]", result).rowcount  # a DELETE returns a CursorResult
    removed_files = media.delete_files(files)  # originals and their WebP copies, unless still shared
    if media.settings.storage == "filesystem":
        try:
            media.sweep_orphans()  # anything else no row claims (crashes, earlier bugs)
        except Exception:  # the purge itself already succeeded
            log.exception("retention: artwork sweep failed")
    log.info(
        "retention: removed %d events, %d artwork files and %d runs older than %s",
        len(expired), removed_files, runs, cutoff,
    )  # fmt: skip
    return len(expired)
