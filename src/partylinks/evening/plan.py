"""Plan my evening: places near an event that are open in each step's window (drinks and dinner before it,
coffee the morning after...), nearest first, and what else is on at the same place this week.

Steps come from ``evening.steps`` (settings.yaml): a step's place genres and its window before the event
starts or after it ends. A place is listed once a day (``partylinks.placecards``), so one place may match
several days: it is kept once, at its opening in the window.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import ColumnElement, Interval, func, literal, or_, select
from sqlalchemy.orm import Session, defer, selectinload

from partylinks.config import Settings
from partylinks.db import EventRow
from partylinks.enrichment import meters
from partylinks.placecards import IS_PLACE, PRICE_LEVEL

# The API's event filters (api.build_event_filters with its settings bound): keyword arguments -> clauses.
Filters = Callable[..., list[ColumnElement[bool]]]
METERS_PER_DEGREE = 111_320  # one degree of latitude


@dataclass
class Step:
    key: str
    items: list[tuple[EventRow, int]] = field(default_factory=list)  # (place listing, meters away)


@dataclass
class Evening:
    steps: list[Step]
    venue: list[EventRow]  # the same place's other listings this week


def _card(query: Any) -> Any:
    return query.options(selectinload(EventRow.images), defer(EventRow.description), defer(EventRow.extra))


def _near(row: EventRow, walk_m: int) -> list[ColumnElement[bool]]:
    """A box around the event (cheap on the table); the exact distance is measured after."""
    assert row.latitude is not None and row.longitude is not None
    dlat = walk_m / METERS_PER_DEGREE
    dlng = walk_m / (METERS_PER_DEGREE * max(math.cos(math.radians(row.latitude)), 0.01))
    return [
        EventRow.latitude.between(row.latitude - dlat, row.latitude + dlat),
        EventRow.longitude.between(row.longitude - dlng, row.longitude + dlng),
    ]


def _place_key(r: EventRow) -> str:
    """One place, whatever page it was listed from: its name, else its page."""
    return (r.venue_name or r.title or r.url).casefold().strip()


def plan(session: Session, cfg: Settings, row: EventRow, filters: Filters, budget: bool = False) -> Evening:
    """``budget``: only places up to ``evening.budget_max_level`` (₪₪) and those with no price given."""
    s = cfg.evening
    cheap = [or_(PRICE_LEVEL.is_(None), PRICE_LEVEL <= s.budget_max_level)] if budget else []
    venue = _venue(session, cfg, row, filters)
    if not s.enabled or row.start_at is None or row.latitude is None or row.longitude is None:
        return Evening([], venue)
    here = (row.latitude, row.longitude)
    end = row.end_at or row.start_at + timedelta(hours=s.assumed_hours)
    steps = []
    # A place once in the whole plan: HappyTLV lists one restaurant under two sections (two pages, two
    # URLs), and a bar with a happy hour is also a dinner place.
    used: set[str] = set()
    for step in s.steps:
        lo, hi = (
            (row.start_at - timedelta(hours=step.hours), row.start_at) if step.when == "before"
            else (end, end + timedelta(hours=step.hours))
        )  # fmt: skip
        # Open some time in [lo, hi): opens before hi and closes after lo (a place with no hours: all day).
        closes = func.coalesce(EventRow.end_at, EventRow.start_at + literal(timedelta(days=1), Interval()))
        rows = session.scalars(_card(
            select(EventRow)
            .where(*filters(genre=step.genres), IS_PLACE, EventRow.start_at < hi, closes > lo,
                   EventRow.url != row.url, *_near(row, s.walk_m), *cheap)
            .order_by(EventRow.start_at, EventRow.id)
        )).all()  # fmt: skip
        nearest: dict[str, tuple[EventRow, int]] = {}
        for r in rows:  # earliest opening of each place
            key = _place_key(r)
            if key in nearest or key in used or r.latitude is None or r.longitude is None:
                continue
            if (d := round(meters(here, (r.latitude, r.longitude)))) <= s.walk_m:
                nearest[key] = (r, d)
        picked = sorted(nearest.values(), key=lambda x: x[1])[: s.per_step]
        used.update(_place_key(r) for r, _ in picked)
        if picked:
            steps.append(Step(step.key, picked))
    return Evening(steps, venue)


def _venue(session: Session, cfg: Settings, row: EventRow, filters: Filters) -> list[EventRow]:
    """The place's other listings (events and its own openings, once each) in the next ``venue_days``."""
    s = cfg.evening
    if not row.venue_slug or not s.venue_items or row.start_at is None:
        return []
    rows: Sequence[EventRow] = session.scalars(_card(
        select(EventRow)
        .where(*filters(venue_slug=row.venue_slug, date_to=row.start_at + timedelta(days=s.venue_days)),
               EventRow.id != row.id, EventRow.url != row.url)
        .order_by(EventRow.start_at, EventRow.id)
    )).all()  # fmt: skip
    seen: set[str] = set()
    out = []
    for r in rows:
        if r.url not in seen:
            seen.add(r.url)
            out.append(r)
    return out[: s.venue_items]
