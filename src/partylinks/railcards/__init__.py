"""A home row shows a show once: one card per title, its nearest date, not one per performance.

A touring show or a film has a listing per date and venue, each sold on many ticket sites, so without
this the busiest ones fill a row with the same poster. Only the rows (/events/rails) do this: lists,
search, filters and the map keep every date.

The title alone is the key. The same show carries a different image per ticket site (a film has 6-10),
so title + image left the repeats in; two different events sharing a title lose one card of a row that
is a sample anyway, and both stay in the lists.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.dialects.postgresql import distinct_on

from partylinks.db import EventRow


def nearest_show(where: Sequence[ColumnElement[bool]]) -> ColumnElement[bool]:
    """The nearest date of each title ``where`` matches; at the same time, the one on the most ticket sites."""
    title = func.lower(func.btrim(EventRow.title))
    nearest = (
        select(EventRow.id)
        .where(*where)
        .ext(distinct_on(title))
        .order_by(title, EventRow.start_at, EventRow.group_size.desc().nulls_last(), EventRow.id)
    )
    return EventRow.id.in_(nearest)


__all__ = ["nearest_show"]
