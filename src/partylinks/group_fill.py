"""Fill what one listing lacks from the other listings of the same event, and the same place.

SPOTX feeds itself from many sources: one event is often sold by several stores (one dedup group),
and one store knows what another leaves out. After each regroup, a listed event that lacks:
- coordinates, venue, address or city takes them from a member of its group that has them (the
  primary first), else coordinates from other events at the same venue (``venue_slug`` + city);
- genres, lineup or minimum age takes them from a member of its group.
Only empty fields are filled; what a source states is never overwritten. The next scrape of a source
writes its own (empty) value again, and the next regroup fills it again.
Pictures are shared at read time (``api._group_covers``), not copied here.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from partylinks.db import EventRow

_COLUMNS = ("latitude", "longitude", "venue_name", "address", "city", "genres", "lineup", "min_age")
_PLACE = ("venue_name", "address", "city")
_SHARED = ("genres", "lineup", "min_age")


def _empty(value: Any) -> bool:
    return value is None or value == [] or value == ""


def plan(session: Session) -> list[dict[str, Any]]:
    """Rows to update (id + every fill column): reads only, so it can run outside the write lock."""
    rows = session.execute(
        select(
            EventRow.id,
            EventRow.group_id,
            EventRow.is_primary,
            EventRow.venue_slug,
            *(getattr(EventRow, c) for c in _COLUMNS),
        ).where(EventRow.is_listed.is_(True))
    ).all()
    current = {r.id: {c: getattr(r, c) for c in _COLUMNS} for r in rows}
    groups: dict[str, list[Any]] = defaultdict(list)
    for r in rows:
        if r.group_id:
            groups[r.group_id].append(r)
    # The place's usual coordinates, by venue and city: most events at the venue agree.
    spots: dict[tuple[str, str | None], Counter[tuple[float, float]]] = defaultdict(Counter)
    for r in rows:
        if r.venue_slug and r.latitude is not None and r.longitude is not None:
            spots[(r.venue_slug, r.city)][(r.latitude, r.longitude)] += 1

    filled = {i: dict(v) for i, v in current.items()}
    for members in groups.values():
        if len(members) < 2:
            continue
        donors = sorted(members, key=lambda m: (not m.is_primary, m.id))
        for m in members:
            row = filled[m.id]
            if row["latitude"] is None or row["longitude"] is None:
                donor = next((d for d in donors if d.latitude is not None and d.longitude is not None), None)
                if donor is not None:
                    row["latitude"], row["longitude"] = donor.latitude, donor.longitude
                    # The place comes with the pin, so a venue never points at another venue's coordinates.
                    for c in _PLACE:
                        if _empty(row[c]):
                            row[c] = getattr(donor, c)
            for c in _SHARED:
                if _empty(row[c]):
                    row[c] = next((getattr(d, c) for d in donors if not _empty(getattr(d, c))), row[c])
    for r in rows:
        row = filled[r.id]
        if (row["latitude"] is None or row["longitude"] is None) and r.venue_slug:
            spot = spots.get((r.venue_slug, row["city"]))
            if spot:
                row["latitude"], row["longitude"] = spot.most_common(1)[0][0]
    return [{"id": i, **row} for i, row in filled.items() if row != current[i]]
