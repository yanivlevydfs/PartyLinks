"""The ``event_views`` table, the counts and the clean-up hooks. On the app's one Base, so
``Database.create_schema`` creates it (partylinks.db imports this module)."""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import Date, String, Text, delete, distinct, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Mapped, Session, mapped_column

import partylinks.saved.models  # through the module: importing saved.models first reaches here half loaded
from partylinks.db import Base, Database, UTCDateTime, utcnow
from partylinks.popularity.config import PopularitySettings

log = logging.getLogger(__name__)

# ponytail: per-process salt, so a deploy mid-day counts a returning visitor once more that day;
# keep the salt in the DB if that ever matters.
_SALT = secrets.token_bytes(16)


class EventViewRow(Base):
    """A visitor opened the event's page that day. No foreign key to events (like saved_events):
    ``delete_views`` removes it with its event."""

    __tablename__ = "event_views"

    event_id: Mapped[str] = mapped_column(Text, primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True, index=True)
    visitor: Mapped[str] = mapped_column(String(16), primary_key=True)
    last_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


def visitor(ip: str | None, agent: str) -> str | None:
    """The visitor's key, or None for a crawler (not a view)."""
    from partylinks.api import CRAWLER  # local: api imports this module

    if not ip or not agent or CRAWLER.search(agent):
        return None
    return hashlib.sha256(_SALT + f"{ip}|{agent}".encode()).hexdigest()[:16]


def record(db: Database, event_id: str, who: str, timezone: str) -> None:
    """On its own connection, not a session: a view must not mark the cached pages stale
    (``Database.changes``) nor queue behind the scraper's writes."""
    now = utcnow()
    row = {"event_id": event_id, "day": now.astimezone(ZoneInfo(timezone)).date(), "visitor": who, "last_at": now}
    try:
        with db.engine.begin() as conn:
            conn.execute(insert(EventViewRow).values(row).on_conflict_do_update(
                index_elements=["event_id", "day", "visitor"], set_={"last_at": now},
            ))  # fmt: skip
    except SQLAlchemyError as exc:  # a lost view is not worth a failed page
        log.warning("view not recorded: %s", exc)


def counts(session: Session, ids: list[str]) -> dict[str, dict[str, int]]:
    """Likes and views of these events: two grouped queries for a whole page of cards."""
    if not ids:
        return {}
    saved = partylinks.saved.models.SavedEventRow
    likes = dict(session.execute(
        select(saved.event_id, func.count()).where(saved.event_id.in_(ids)).group_by(saved.event_id)
    ).all())  # fmt: skip
    views = dict(session.execute(
        select(EventViewRow.event_id, func.count()).where(EventViewRow.event_id.in_(ids)).group_by(EventViewRow.event_id)
    ).all())  # fmt: skip
    return {i: {"likes": likes.get(i, 0), "views": views.get(i, 0)} for i in ids}


def watching(session: Session, event_id: str, cfg: PopularitySettings) -> int:
    """Distinct visitors with a view in the last ``watching_minutes``; 0 below ``min_watching``."""
    since = utcnow() - timedelta(minutes=cfg.watching_minutes)
    n = session.scalar(
        select(func.count(distinct(EventViewRow.visitor))).where(EventViewRow.event_id == event_id, EventViewRow.last_at >= since)
    ) or 0  # fmt: skip
    return n if n >= cfg.min_watching else 0


def delete_views(session: Session, event_ids: list[str]) -> None:
    """Called by ``db.delete_events``: views go with their event (never past events)."""
    session.execute(delete(EventViewRow).where(EventViewRow.event_id.in_(event_ids)))


def prune(db: Database, cfg: PopularitySettings, timezone: str) -> None:
    """Called with retention: view rows older than ``keep_days``."""
    cutoff = datetime.now(ZoneInfo(timezone)).date() - timedelta(days=cfg.keep_days)
    with db.session() as session:
        session.execute(delete(EventViewRow).where(EventViewRow.day < cutoff))
