"""Israeli holidays (Hanukkah, Purim, Pesach, Independence Day, Shavuot, Rosh Hashanah, Sukkot, Lag BaOmer, Tu
BeAv, Silvester...): their next dates, from the search's own calendar (``search.HOLIDAY_NAMES``, eve to last
day), and the events on them. Each holiday has a page (/holiday/<key>) once it has enough events.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from partylinks.config import Settings
from partylinks.db import EventRow, upcoming_filter
from partylinks.placecards import IS_PLACE
from partylinks.search import HOLIDAY_NAMES, _next_holiday

_HEBREW = re.compile(r"[֐-׿]")


@dataclass(frozen=True)
class Holiday:
    key: str
    he: str
    en: str
    start: date  # the eve (parties start there), or today once it has begun
    end: date

    def name(self, lang: str) -> str:
        return self.he if lang == "he" else self.en


def _names(key: str) -> tuple[str, str]:
    """(Hebrew, English) of a holiday: its first name in each script ("חנוכה", "Hanukkah")."""
    names = HOLIDAY_NAMES[key]
    he = next(n for n in names if _HEBREW.search(n))
    en = next((n for n in names if not _HEBREW.search(n)), key.replace("_", " "))
    return he, en.title()


def upcoming(cfg: Settings, today: date) -> list[Holiday]:
    """Every holiday from today to ``ahead_days`` ahead (its next dates), soonest first."""
    if not cfg.holiday_pages.enabled:
        return []
    horizon = today + timedelta(days=cfg.holiday_pages.ahead_days)
    out = []
    for key in HOLIDAY_NAMES:
        start, end = _next_holiday(today, key)
        if start <= horizon:
            out.append(Holiday(key, *_names(key), start, end))
    return sorted(out, key=lambda h: (h.start, h.key))


def where(h: Holiday) -> list[ColumnElement[bool]]:
    """Events on the holiday's dates (not places open every day)."""
    return [EventRow.local_date >= h.start, EventRow.local_date <= h.end, ~IS_PLACE]


def counts(session: Session, holidays: Sequence[Holiday]) -> dict[str, int]:
    """Upcoming events in Israel on each holiday, one query for all of them."""
    if not holidays:
        return {}
    first, last = min(h.start for h in holidays), max(h.end for h in holidays)
    query = (
        select(EventRow.local_date, func.count())
        .where(upcoming_filter(), EventRow.is_primary.is_(True), EventRow.country.is_(None), ~IS_PLACE,
               EventRow.local_date >= first, EventRow.local_date <= last)
        .group_by(EventRow.local_date)
    )  # fmt: skip
    per_day: dict[Any, int] = {day: n for day, n in session.execute(query).all()}
    return {
        h.key: sum(per_day.get(h.start + timedelta(days=n), 0) for n in range((h.end - h.start).days + 1))
        for h in holidays
    }


def with_pages(session: Session, cfg: Settings, today: date) -> list[tuple[Holiday, int]]:
    """Upcoming holidays with enough events for a page, and their event counts."""
    holidays = upcoming(cfg, today)
    n = counts(session, holidays)
    return [(h, n[h.key]) for h in holidays if n[h.key] >= cfg.holiday_pages.min_events]
