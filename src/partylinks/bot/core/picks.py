""""Interesting events": the weekly digest's and the broadcast channel's (phase 2) selection. Upcoming
events of the weekend (or tonight) matching the words, the most clicked in the last week first, then those
with a picture, then the soonest. One show once (its soonest date)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, defer, selectinload

from partylinks.config import Settings
from partylinks.db import ClickRow, EventRow, utcnow

CLICKS_DAYS = 7
OVERSHOOT = 4  # rows read per pick: room for repeats of a show


def picks(
    session: Session, cfg: Settings, query: str | None, when: Literal["weekend", "tonight"], today: date, limit: int,
    exclude: set[str] | None = None,
) -> list[EventRow]:  # fmt: skip
    from partylinks.api import build_event_filters  # local: api imports the bot

    dated = {"weekend": True} if when == "weekend" else {"on_date": today}
    filters = build_event_filters(cfg, q=query or None, **dated)  # type: ignore[arg-type]
    if when == "weekend":  # this weekend, not every weekend ahead
        filters.append(EventRow.local_date <= today + timedelta(days=(5 - today.weekday()) % 7))  # to Saturday
    clicks = (
        select(ClickRow.event_id, func.count().label("n"))
        .where(ClickRow.at >= utcnow() - timedelta(days=CLICKS_DAYS)).group_by(ClickRow.event_id).subquery()
    )  # fmt: skip
    rows = session.scalars(
        select(EventRow).outerjoin(clicks, clicks.c.event_id == EventRow.id).where(*filters)
        .order_by(func.coalesce(clicks.c.n, 0).desc(), EventRow.image_url.is_(None), EventRow.start_at.asc().nulls_last(), EventRow.id)
        .limit(limit * OVERSHOOT)
        .options(selectinload(EventRow.images), defer(EventRow.description), defer(EventRow.extra))
    ).all()  # fmt: skip
    out: list[EventRow] = []
    titles: set[str] = set()
    for row in rows:
        if row.id in (exclude or set()) or row.title.casefold() in titles:
            continue
        out.append(row)
        titles.add(row.title.casefold())
        if len(out) == limit:
            break
    return out
