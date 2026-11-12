"""Pinning: see the package docstring."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ColumnElement, exists, or_, select
from sqlalchemy.orm import Session

from partylinks.db import EventRow
from partylinks.home_pins.models import HomePinRow

log = logging.getLogger(__name__)
Wheres = dict[str, list[ColumnElement[bool]]]

# Each row's filters at the last /events/rails build ("trending": upcoming only), for the admin's "where it shows".
# ponytail: per process and as old as the cached rows (a minute); filled on startup by the warm-up.
LAST: dict[str, Wheres] = {"rows": {}}


def same_event(event_id: str) -> ColumnElement[bool]:
    """Any listing of ``event_id``'s duplicate group (the pk and the group_id index)."""
    group = select(EventRow.group_id).where(EventRow.id == event_id).scalar_subquery()
    return or_(EventRow.id == event_id, EventRow.group_id == group)


def active(session: Session) -> list[HomePinRow]:
    """Pins not past their ``until``, first shown first (an event over drops out at load: upcoming only)."""
    now = datetime.now(UTC)
    where = or_(HomePinRow.until.is_(None), HomePinRow.until > now)
    return list(session.scalars(select(HomePinRow).where(where).order_by(HomePinRow.position, HomePinRow.created_at)))


def matches(session: Session, event_id: str) -> dict[str, bool]:
    """{row key: the event meets the row's own filters}, one query; "trending" = it is upcoming."""
    rows, this = LAST["rows"], same_event(event_id)
    if not rows:
        return {}
    found = session.execute(select(*(exists().where(*w, this).label(k) for k, w in rows.items()))).one()
    return dict(zip(rows, found, strict=True))


def shown_in(session: Session, pin: HomePinRow) -> list[str]:
    """The rows ``pin`` leads: its chosen ones (whatever their filters), else Trending and every row it matches."""
    found = matches(session, pin.event_id)
    if not found.get("trending"):  # over, delisted or deleted
        return []
    return [k for k in pin.rails if k in found] if pin.rails else [k for k, ok in found.items() if ok]


def pin(
    session: Session, rails: dict[str, list[Any]], wheres: Wheres, upcoming: list[ColumnElement[bool]],
    load: Callable[[list[ColumnElement[bool]], int], list[Any]],
) -> None:  # fmt: skip
    """Put every pinned event first in the rows it is shown in. Never breaks the rows: on any error they
    are served unpinned."""
    LAST["rows"] = {**wheres, "trending": upcoming}
    try:
        with session.begin_nested():
            pinned: dict[str, list[Any]] = {}
            for p in reversed(active(session)):  # the first pin ends up first
                keys = [k for k in shown_in(session, p) if k in rails]
                if keys and (found := load([*upcoming, same_event(p.event_id)], 1)):
                    card = found[0].model_copy(update={"pinned": True})
                    for k in keys:
                        pinned[k] = [card, *(e for e in pinned.get(k, rails[k]) if e.id != card.id)]
        rails.update(pinned)
    except Exception:
        log.exception("home pins: pinning failed, rows served unpinned")
