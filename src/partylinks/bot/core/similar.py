"""🔁 under a card: upcoming events like this one. Its genres in its area, else its genres anywhere, else
whatever is on in its area; never the event itself or another listing of it (its group)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, defer, selectinload

from partylinks.config import Settings
from partylinks.db import EventRow


def find(session: Session, cfg: Settings, row: EventRow, limit: int) -> list[EventRow]:
    from partylinks.api import _IS_PLACE, build_event_filters  # local: api imports the bot

    other = [EventRow.id != row.id] + ([EventRow.group_id != row.group_id] if row.group_id else [])
    tries: list[dict[str, Any]] = []
    if row.genres and row.area:
        tries.append({"genre": row.genres, "area": [row.area]})
    if row.genres:
        tries.append({"genre": row.genres, "country": row.country} if row.country else {"genre": row.genres})
    if row.area:
        tries.append({"area": [row.area]})
    for given in tries:
        filters = build_event_filters(cfg, anywhere=True, **given) + other
        rows = list(session.scalars(
            select(EventRow).where(*filters)
            .order_by(EventRow.local_date.asc().nulls_last(), _IS_PLACE, EventRow.start_at.asc().nulls_last(), EventRow.id)
            .limit(limit).options(selectinload(EventRow.images), defer(EventRow.description), defer(EventRow.extra))
        ))  # fmt: skip
        if rows:
            return rows
    return []
