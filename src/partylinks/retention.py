"""Retention: purge events that are over (with tickets and artwork), old run history, and
give the freed space back to the OS."""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, delete, or_, select

from partylinks.config import RetentionSettings
from partylinks.db import Database, EventRow, ScrapeRunRow, delete_events
from partylinks.media import MediaStore

log = logging.getLogger(__name__)


def retention_cutoff(settings: RetentionSettings, timezone: str, now: datetime | None = None) -> datetime:
    """Local midnight ``keep_days_back`` days ago (1 = keep yesterday onwards)."""
    tz = ZoneInfo(timezone)
    today = (now or datetime.now(tz)).astimezone(tz).date()
    return datetime.combine(today - timedelta(days=settings.keep_days_back), time.min, tzinfo=tz)


def purge_expired(db: Database, media: MediaStore, settings: RetentionSettings, timezone: str) -> int:
    """Delete events that started before the cutoff (local midnight, today - keep_days_back).

    The start date decides, not the end: some listings carry an end date weeks later
    (e.g. a recurring "season" page) and must not linger as if they were upcoming.
    """
    cutoff = retention_cutoff(settings, timezone)
    finished = EventRow.start_at
    with db.session() as session:
        expired = session.scalars(
            select(EventRow.id).where(
                or_(
                    finished < cutoff,
                    # Undated events disappear once the source stops listing them.
                    and_(EventRow.start_at.is_(None), EventRow.last_seen_at < cutoff),
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
