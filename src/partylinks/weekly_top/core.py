"""This week's top: the most wanted events of the coming days, by our own signals (saves, page views, how many
ticket sites sell it), and the rising genres (most events newly listed). Read live; the page is cached."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, defer, selectinload

from partylinks.config import Settings
from partylinks.db import EventRow, upcoming_filter
from partylinks.placecards import IS_PLACE
from partylinks.popularity.models import EventViewRow
from partylinks.saved.models import SavedEventRow


def _base(cfg: Settings, now: datetime) -> list:
    """Upcoming events in Israel in the next ``days`` (one listing each, no places)."""
    return [upcoming_filter(), EventRow.is_primary.is_(True), EventRow.country.is_(None), ~IS_PLACE,
            EventRow.start_at <= now + timedelta(days=cfg.weekly_top.days)]  # fmt: skip


def top(session: Session, cfg: Settings, now: datetime) -> list[EventRow]:
    """The most wanted events of the coming ``days``, best first. ``now``: aware."""
    s = cfg.weekly_top
    if not s.enabled:
        return []
    likes = select(SavedEventRow.event_id, func.count().label("n")).group_by(SavedEventRow.event_id).subquery()
    since = (now - timedelta(days=s.views_days)).date()
    views = (
        select(EventViewRow.event_id, func.count().label("n"))
        .where(EventViewRow.day >= since)
        .group_by(EventViewRow.event_id)
        .subquery()
    )
    score = (
        s.like_weight * func.coalesce(likes.c.n, 0)
        + s.view_weight * func.coalesce(views.c.n, 0)
        + s.listing_weight * (func.coalesce(EventRow.group_size, 1) - 1)
    )
    return list(session.scalars(
        select(EventRow)
        .outerjoin(likes, likes.c.event_id == EventRow.id)
        .outerjoin(views, views.c.event_id == EventRow.id)
        .where(*_base(cfg, now))
        .order_by(score.desc(), EventRow.start_at, EventRow.id)
        .limit(s.limit)
        .options(selectinload(EventRow.images), defer(EventRow.description), defer(EventRow.extra))
    ))  # fmt: skip


def rising(session: Session, cfg: Settings, now: datetime) -> list[tuple[str, int]]:
    """(genre, events first listed in the last ``rising_days``), most first."""
    s = cfg.weekly_top
    if not s.enabled:
        return []
    since = now - timedelta(days=s.rising_days)
    counts: Counter[str] = Counter()
    for genres in session.scalars(
        select(EventRow.genres).where(upcoming_filter(), EventRow.is_primary.is_(True), EventRow.country.is_(None),
                                      ~IS_PLACE, EventRow.first_seen_at >= since)
    ):  # fmt: skip
        counts.update(genres or [])
    return counts.most_common(s.rising_limit)
