"""Plan my evening: places near an event that are open in each step's window (drinks and dinner before it,
a bar or late food after...), nearest first, and what else is on at the same place this week.

Steps come from ``evening.steps`` (settings.yaml): a step's place genres and its window before the event
starts or after it ends. A place is listed once a day (``partylinks.placecards``), so one place may match
several days: it is kept once, at its opening in the window. After the event a place counts when its opening
hours (``extra.hours``, Google's via HappyTLV) reach into the window: a bar's listing is its happy-hour slot
(17:00-20:00), not the hours it is open (till 02:00), and tonight's slot is gone once it ended (past listings are
deleted), so its listing of the day before or after stands for it. Before it, and with no hours known, the
listing's own time.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, Interval, func, literal, or_, select
from sqlalchemy.orm import Session, defer, selectinload

from partylinks.config import Settings
from partylinks.db import EventRow
from partylinks.enrichment import meters
from partylinks.placecards import IS_PLACE, PRICE_LEVEL
from partylinks.placeinfo.pages import DAYS

# The API's event filters (api.build_event_filters with its settings bound): keyword arguments -> clauses.
Filters = Callable[..., list[ColumnElement[bool]]]
METERS_PER_DEGREE = 111_320  # one degree of latitude
DAY = timedelta(days=1)
log = logging.getLogger(__name__)
_bad_hours: set[str] = set()  # places whose hours could not be read, logged once each


Span = tuple[datetime, datetime]


@dataclass
class Step:
    key: str
    items: list[tuple[EventRow, int]] = field(default_factory=list)  # (place listing, meters away)
    opens: dict[str, Span] = field(default_factory=dict)  # listing id -> its opening hours in the window (after)


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


def _opening(hours: dict[str, list[str]], lo: datetime, hi: datetime, tz: tzinfo) -> Span | None:
    """The first opening of weekly hours ({"Friday": ["18:00-03:00"]}) some time in [lo, hi); one past
    midnight closes the next day, so the day before ``lo`` counts too."""
    first, last = lo.astimezone(tz).date(), hi.astimezone(tz).date()
    for day in (first + timedelta(days=n) for n in range(-1, (last - first).days + 1)):
        for span in hours.get(DAYS[(day.weekday() + 1) % 7]) or []:
            opens, closes = (time.fromisoformat(t) for t in span.replace("24:00", "00:00").split("-"))
            start = datetime.combine(day, opens, tz)
            end = datetime.combine(day + timedelta(days=closes <= opens), closes, tz)
            if start < hi and end > lo:
                return start, end
    return None


def _open_after(r: EventRow, hours: Any, lo: datetime, hi: datetime, tz: tzinfo) -> Span | bool:
    """After the event: the place's opening in [lo, hi) by its hours when known (False: closed then); with no
    hours, whether the listing's own time is in it (no end: all day)."""
    if not hours:
        return r.start_at < hi and (r.end_at or r.start_at + timedelta(days=1)) > lo
    try:
        return _opening(hours, lo, hi, tz) or False
    except (AttributeError, TypeError, ValueError) as exc:  # odd hours: skip the place, not the plan
        if r.url not in _bad_hours:
            _bad_hours.add(r.url)
            log.warning("evening: hours of %s unreadable (%r): %s", r.url, hours, exc)
        return False


def plan(session: Session, cfg: Settings, row: EventRow, filters: Filters, budget: bool = False) -> Evening:
    """``budget``: only places up to ``evening.budget_max_level`` (₪₪) and those with no price given."""
    s = cfg.evening
    cheap = [or_(PRICE_LEVEL.is_(None), PRICE_LEVEL <= s.budget_max_level)] if budget else []
    venue = _venue(session, cfg, row, filters)
    if not s.enabled or row.start_at is None or row.latitude is None or row.longitude is None:
        return Evening([], venue)
    here, tz = (row.latitude, row.longitude), ZoneInfo(cfg.timezone)
    end = row.end_at or row.start_at + timedelta(hours=s.assumed_hours)
    steps = []
    # A place once in the whole plan: HappyTLV lists one restaurant under two sections (two pages, two
    # URLs), and a bar with a happy hour is also a dinner place.
    used: set[str] = set()
    for step in s.steps:
        after = step.when == "after"
        span = timedelta(hours=step.hours)
        lo, hi = (end, end + span) if after else (row.start_at - span, row.start_at)
        # Open some time in [lo, hi): opens before hi and closes after lo (a place with no hours: all day).
        # After: its listings from a day before lo to a day after, the nearest first; _open_after decides.
        closes = func.coalesce(EventRow.end_at, EventRow.start_at + literal(DAY, Interval()))
        near_lo = (EventRow.start_at > lo - DAY, EventRow.start_at < lo + DAY)
        when = near_lo if after else (EventRow.start_at < hi, closes > lo)
        rows = session.execute(_card(
            select(EventRow, EventRow.extra["hours"])
            .where(*filters(genre=step.genres), IS_PLACE, *when, EventRow.url != row.url, *_near(row, s.walk_m), *cheap)
            .order_by(func.abs(func.extract("epoch", EventRow.start_at - lo)) if after else EventRow.start_at, EventRow.id)
        )).all()  # fmt: skip
        nearest: dict[str, tuple[EventRow, int]] = {}
        opens: dict[str, Span] = {}
        for r, hours in rows:  # one listing of each place: the earliest before, the nearest after
            key = _place_key(r)
            if key in nearest or key in used or r.latitude is None or r.longitude is None:
                continue
            if after:
                if not (found := _open_after(r, hours, lo, hi, tz)):
                    continue
                if found is not True:
                    opens[r.id] = found
            if (d := round(meters(here, (r.latitude, r.longitude)))) <= s.walk_m:
                nearest[key] = (r, d)
        picked = sorted(nearest.values(), key=lambda x: x[1])[: s.per_step]
        used.update(_place_key(r) for r, _ in picked)
        if picked:
            steps.append(Step(step.key, picked, {r.id: opens[r.id] for r, _ in picked if r.id in opens}))
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
