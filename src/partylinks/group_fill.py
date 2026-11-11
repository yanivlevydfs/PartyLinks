"""Fill what one listing lacks from the other listings of the same event, and the same place.

SPOTX feeds itself from many sources: one event is often sold by several stores (one dedup group),
and one store knows what another leaves out. After each regroup, a listed event that lacks:
- coordinates, venue, address or city takes them from a member of its group that has them (the
  primary first), else coordinates from other events at the same venue (``venue_slug`` + city);
- genres, lineup or minimum age takes them from a member of its group.
Only empty fields are filled; what a source states is never overwritten, except the place: a city's own
event page (``place_sources``, dedup.place_sources) gives every listing of its event its place and pin,
since the city's box office lists a default hall (Herzliya's Smarticket: "מרכז הקיימות" for a talk at a cafe). The next scrape of a source
writes its own (empty) value again, and the next regroup fills it again.
Pictures are shared at read time (``api._group_covers``), not copied here. Stored lineups are also passed
through the current name rule, so junk saved before a rule was tightened leaves every event at once.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from partylinks.db import EventRow
from partylinks.parsing import slugify

_COLUMNS = ("latitude", "longitude", "venue_name", "venue_slug", "address", "city", "genres", "lineup", "min_age")
_PIN = ("latitude", "longitude", "venue_name", "venue_slug", "address", "city")
_PLACE = ("venue_name", "address", "city")
_SHARED = ("genres", "lineup", "min_age")


def _empty(value: Any) -> bool:
    return value is None or value == [] or value == ""


def plan(
    session: Session,
    clean_name: Callable[[str], str | None] | None = None,
    place_sources: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Rows to update (id + every fill column): reads only, so it can run outside the write lock. ``clean_name``
    (the enrichment's name rule) cleans what stored lineups hold from before a rule was tightened: "01] DJ IAGO"
    -> "DJ IAGO", "00h00 - 06h30" dropped."""
    rows = session.execute(
        select(
            EventRow.id,
            EventRow.group_id,
            EventRow.is_primary,
            EventRow.source,
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
    if clean_name is not None:
        for row in filled.values():
            if row["lineup"]:
                row["lineup"] = list(dict.fromkeys(c for n in row["lineup"] if (c := clean_name(n))))
    for members in groups.values():
        if len(members) < 2:
            continue
        donors = sorted(members, key=lambda m: (not m.is_primary, m.id))
        city = next(
            (d for d in donors if d.source in place_sources and d.venue_name and d.latitude is not None and d.longitude is not None),
            None,
        )
        for m in members:
            row = filled[m.id]
            if city is not None and m.source not in place_sources:
                for c in _PIN:  # the city's own page: where its event really is
                    row[c] = getattr(city, c)
                row["venue_slug"] = row["venue_slug"] or slugify(row["venue_name"]) or None
            if row["latitude"] is None or row["longitude"] is None:
                # A listing naming a place of its own is geocoded at it, never pinned at another listing's venue
                # (that pin would stop the geocoder: a cafe shown at the city's culture centre).
                donor = next(
                    (
                        d for d in donors
                        if d.latitude is not None and d.longitude is not None
                        and (_empty(row["venue_name"]) and _empty(row["address"]) or row["venue_slug"] == d.venue_slug)
                    ),
                    None,
                )
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
