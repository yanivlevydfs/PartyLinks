"""Recurring lines: an event whose title comes back at the same place on the same weekday, on at least
``lines.min_dates`` upcoming dates, is a weekly line; its card says so ("🔁 every Thursday").

Read from the listings themselves, one grouped query per card list: no stored state to keep in sync.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select, tuple_
from sqlalchemy.orm import Session

from partylinks.config import Settings
from partylinks.db import EventRow, upcoming_filter
from partylinks.placecards import IS_PLACE


def weekly(session: Session, cfg: Settings, rows: Sequence[Any]) -> dict[str, str]:
    """{id: weekday ("thursday")} for the cards among ``rows`` that are a weekly line (places are not)."""
    if not cfg.lines.enabled:
        return {}
    keys = {(r.venue_slug, r.title, r.day_of_week) for r in rows if r.venue_slug and r.day_of_week}
    if not keys:
        return {}
    found = session.execute(
        select(EventRow.venue_slug, EventRow.title, EventRow.day_of_week)
        .where(upcoming_filter(), EventRow.is_primary.is_(True), ~IS_PLACE,
               tuple_(EventRow.venue_slug, EventRow.title, EventRow.day_of_week).in_(keys))
        .group_by(EventRow.venue_slug, EventRow.title, EventRow.day_of_week)
        .having(func.count(func.distinct(EventRow.local_date)) >= cfg.lines.min_dates)
    ).all()  # fmt: skip
    lines = {tuple(k) for k in found}
    return {r.id: r.day_of_week for r in rows if (r.venue_slug, r.title, r.day_of_week) in lines}
