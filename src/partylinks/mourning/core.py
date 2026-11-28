"""The days of mourning's dates (Israel's law and custom) and the clause that keeps their parties out of promotion.

A day's hours: from its eve at ``eve_hour`` to the day at its ``end_hour`` (Israel time). ``clauses`` gives the
WHERE that a promoted list (home rows, the week's top, the bot's highlights, paid picks) adds while those hours
are on (or ``ahead_hours`` before): no event of ``genres`` starting in them. ``tag`` changes when they begin and
end, so the cached home rows (home_rails.RowCache) rebuild then.
"""

from __future__ import annotations

import json
import warnings
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from typing import NamedTuple
from zoneinfo import ZoneInfo

from convertdate import hebrew
from sqlalchemy import ColumnElement, String, and_, cast, not_, or_

from partylinks.config import Settings
from partylinks.db import EventRow
from partylinks.search import _holiday

_DAY = timedelta(days=1)


def _hebrew(year: int, month: int, day: int) -> date:
    """A Hebrew date in a Gregorian year (Nisan to Elul fall in Hebrew year ``year + 3760``, Tishrei on in +3761)."""
    with warnings.catch_warnings():  # convertdate 2.5.1 calls its own deprecated month_days()
        warnings.filterwarnings("ignore", "month_days is deprecated", DeprecationWarning)
        return date(*hebrew.to_gregorian(year + (3761 if month >= hebrew.TISHRI else 3760), month, day))


def yom_hashoah(year: int) -> date:
    """27 Nisan; on a Friday the Thursday before (26), on a Sunday the Monday after (28)."""
    d = _hebrew(year, hebrew.NISAN, 27)
    return d + {4: -_DAY, 6: _DAY}.get(d.weekday(), 0 * _DAY)


def memorial_day(year: int) -> date:
    """The day before Independence Day (search's rule: 5 Iyar, moved off Friday / Saturday / Monday)."""
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", "month_days is deprecated", DeprecationWarning)
        return _holiday(year, "independence_day")[0]


def tisha_bav(year: int) -> date:
    """9 Av; on a Shabbat the Sunday after (10 Av)."""
    d = _hebrew(year, hebrew.AV, 9)
    return d + (_DAY if d.weekday() == 5 else 0 * _DAY)


def yom_kippur(year: int) -> date:
    """10 Tishrei."""
    return _hebrew(year, hebrew.TISHRI, 10)


RULES: dict[str, Callable[[int], date]] = {
    "yom_hashoah": yom_hashoah,
    "memorial_day": memorial_day,
    "tisha_bav": tisha_bav,
    "yom_kippur": yom_kippur,
}


class Window(NamedTuple):
    key: str
    start: datetime  # aware, the eve at eve_hour
    end: datetime  # aware, exclusive


def windows(cfg: Settings, year: int) -> list[Window]:
    """Each configured day's hours in a Gregorian year, by start."""
    s, tz = cfg.mourning, ZoneInfo(cfg.timezone)
    out = []
    for key, day in s.days.items():
        d = RULES[key](year)
        start = datetime.combine(d - _DAY, time(s.eve_hour), tzinfo=tz)
        end = datetime.combine(d, time(), tzinfo=tz) + timedelta(hours=day.end_hour)
        out.append(Window(key, start, end))
    return sorted(out, key=lambda w: w.start)


def current(cfg: Settings, now: datetime) -> list[Window]:
    """The days whose hours are on at ``now`` (aware), or begin within ``ahead_hours``."""
    s = cfg.mourning
    if not s.enabled or not s.days or not s.genres:
        return []
    soon = now + timedelta(hours=s.ahead_hours)
    local = now.astimezone(ZoneInfo(cfg.timezone))
    return [w for y in (local.year, local.year + 1) for w in windows(cfg, y) if w.start <= soon and w.end > now]


def clauses(cfg: Settings, now: datetime) -> list[ColumnElement[bool]]:
    """[] or one clause: not a party (``genres`` and their sub-genres) starting in a current day's hours."""
    on = current(cfg, now)
    if not on:
        return []
    genres = cfg.enrichment.expand_genres(cfg.mourning.genres)
    party = or_(*(cast(EventRow.genres, String).ilike(f'%"{g}"%') for g in genres))
    starts = or_(
        *(and_(EventRow.start_at >= w.start.astimezone(UTC), EventRow.start_at < w.end.astimezone(UTC)) for w in on)
    )
    return [or_(EventRow.start_at.is_(None), EventRow.genres.is_(None), not_(and_(party, starts)))]


def tag(cfg: Settings, now: datetime) -> str:
    """Empty, or the current days and genres: a home row's cache tag changes when a day's hours begin or end."""
    on = current(cfg, now)
    return json.dumps([[w.key, w.start.isoformat()] for w in on] + [cfg.mourning.genres]) if on else ""
