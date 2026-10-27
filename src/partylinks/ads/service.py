"""Sponsored events first in the results: which paid picks lead a list (``GET /events?sponsored=true``)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import NamedTuple

from sqlalchemy import ColumnElement, Select, and_, or_, select
from sqlalchemy.orm import Session

from partylinks.ads.models import SponsoredRow
from partylinks.db import EventRow, flag_on, upcoming_filter


class Promoted(NamedTuple):
    first: list[tuple[EventRow, str]]  # page 1's paid cards, each with its placement's event id
    drop: list[str]  # their ids, left out of the plain list on every page


def promoted_for(
    session: Session, card: Select[tuple[EventRow]], filters: list[ColumnElement[bool]], page: int, cap: int
) -> Promoted:
    """Up to ``cap`` picks with placement results / both, campaign not over, that pass the list's own
    ``filters`` (the very WHERE of the list), by start time, as their duplicate group's primary
    card. Nothing while the admin's sponsored switch is off. Three small queries, nothing per row."""
    if cap < 1 or not flag_on(session, "sponsored"):
        return Promoted([], [])
    picks = session.execute(
        select(SponsoredRow.event_id, EventRow.group_id)
        .join(EventRow, EventRow.id == SponsoredRow.event_id)
        .where(
            SponsoredRow.placement.in_(("results", "both")),
            or_(SponsoredRow.until.is_(None), SponsoredRow.until > datetime.now(UTC)),
        )
    ).all()
    by_group = {g: i for i, g in sorted(picks, reverse=True) if g}  # a group picked twice: always the same id
    if not by_group:
        return Promoted([], [])
    # The filters are a column here, not the WHERE: in WHERE, SQLite's planner (no ANALYZE) walks an
    # index of a filter column (is_primary, country...) over the whole table instead of the group's.
    ok = and_(*filters, upcoming_filter(), EventRow.is_primary.is_(True)).label("ok")
    rows = session.execute(card.add_columns(ok).where(EventRow.group_id.in_(by_group))).all()
    paid = sorted(
        (r for r, match in rows if match), key=lambda r: (r.start_at or datetime.max.replace(tzinfo=UTC), r.id)
    )[:cap]
    first = [(r, by_group[r.group_id or ""]) for r in paid] if page == 1 else []
    return Promoted(first, [r.id for r in paid])
