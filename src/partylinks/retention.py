"""Retention: purge events that are over, together with their tickets and artwork files."""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select

from partylinks.config import RetentionSettings
from partylinks.db import Database, EventRow, delete_events
from partylinks.media import MediaStore

log = logging.getLogger(__name__)


def retention_cutoff(settings: RetentionSettings, timezone: str, now: datetime | None = None) -> datetime:
    """Local midnight ``keep_days_back`` days ago (1 = keep yesterday onwards)."""
    tz = ZoneInfo(timezone)
    today = (now or datetime.now(tz)).astimezone(tz).date()
    return datetime.combine(today - timedelta(days=settings.keep_days_back), time.min, tzinfo=tz)


def purge_expired(db: Database, media: MediaStore, settings: RetentionSettings, timezone: str) -> int:
    """Delete events that ended before the cutoff. Returns the number of events removed."""
    cutoff = retention_cutoff(settings, timezone)
    finished = func.coalesce(EventRow.end_at, EventRow.start_at)
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
    removed_files = media.delete_files(files)
    log.info("retention: removed %d events and %d artwork files older than %s", len(expired), removed_files, cutoff)
    return len(expired)
