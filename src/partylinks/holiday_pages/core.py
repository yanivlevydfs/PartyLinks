"""Holidays in Israel (Hanukkah, Purim, Pesach, Independence Day, Shavuot, Rosh Hashanah, Sukkot, Lag BaOmer, Tu
BeAv, Silvester, the Mimouna, Christmas, Ramadan, the Eids...): their next dates, from the search's own calendar
(names: ``holiday_pages.names``; dates: ``search._holiday``, eve to last day), and the events on them. A season holiday
(``holiday_pages.seasons``: Christmas, Ramadan) lists only its own events (its genre) in its season. Each holiday
has a page (/holiday/<key>) once it has enough events.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.orm import Session

from partylinks.config import Settings
from partylinks.db import EventRow, upcoming_filter
from partylinks.placecards import IS_PLACE
from partylinks.search import _genre as search_genre
from partylinks.search import _next_holiday

_HEBREW = re.compile(r"[֐-׿]")
_LATIN = re.compile(r"[A-Za-z]")


@dataclass(frozen=True)
class Holiday:
    key: str
    he: str
    en: str
    start: date  # the eve (parties start there), or today once it has begun
    end: date
    genre: str | None = None  # a season holiday: only its own events (holiday_pages.seasons)

    def name(self, lang: str) -> str:
        return self.he if lang == "he" else self.en


def _names(names: list[str], key: str) -> tuple[str, str]:
    """(Hebrew, English) of a holiday: its first Hebrew and its first Latin name ("חנוכה", "Hanukkah"); never an
    Arabic one ("رمضان" is a search word, not a title)."""
    he = next(n for n in names if _HEBREW.search(n))
    en = next((n for n in names if _LATIN.search(n) and not _HEBREW.search(n)), key.replace("_", " "))
    return he, en.title()


def upcoming(cfg: Settings, today: date) -> list[Holiday]:
    """Every holiday from today to ``ahead_days`` ahead (its next dates), soonest first."""
    if not cfg.holiday_pages.enabled:
        return []
    horizon = today + timedelta(days=cfg.holiday_pages.ahead_days)
    out = []
    for key, names in cfg.holiday_pages.names.items():
        span = _next_holiday(today, key, cfg.holiday_pages.seasons)
        if span is None:  # no known dates left (scripts/fetch_holiday_dates.py)
            continue
        start, end = span
        if start <= horizon:
            season = cfg.holiday_pages.seasons.get(key)
            out.append(Holiday(key, *_names(names, key), start, end, season.genre if season else None))
    return sorted(out, key=lambda h: (h.start, h.key))


def where(h: Holiday, cfg: Settings) -> list[ColumnElement[bool]]:
    """Events on the holiday's dates (not places open every day); a season holiday's own events only."""
    out = [EventRow.local_date >= h.start, EventRow.local_date <= h.end, ~IS_PLACE]
    if h.genre:  # JSONB containment: served by the genres GIN index
        out.append(search_genre(cfg, h.genre))
    return out


def counts(session: Session, holidays: Sequence[Holiday], cfg: Settings) -> dict[str, int]:
    """Upcoming events in Israel on each holiday: one query for the season holidays (a count each), one for the
    dated ones."""
    if not holidays:
        return {}
    base = [upcoming_filter(), EventRow.is_primary.is_(True), EventRow.country.is_(None)]
    season_list = [h for h in holidays if h.genre]
    seasons: dict[str, int] = {}
    if season_list:
        row = session.execute(
            select(*(func.count().filter(and_(*where(h, cfg))) for h in season_list)).select_from(EventRow).where(*base)
        ).one()
        seasons = {h.key: n or 0 for h, n in zip(season_list, row, strict=True)}
    holidays = [h for h in holidays if not h.genre]
    if not holidays:
        return seasons
    first, last = min(h.start for h in holidays), max(h.end for h in holidays)
    query = (
        select(EventRow.local_date, func.count())
        .where(upcoming_filter(), EventRow.is_primary.is_(True), EventRow.country.is_(None), ~IS_PLACE,
               EventRow.local_date >= first, EventRow.local_date <= last)
        .group_by(EventRow.local_date)
    )  # fmt: skip
    per_day: dict[Any, int] = {day: n for day, n in session.execute(query).all()}
    return seasons | {
        h.key: sum(per_day.get(h.start + timedelta(days=n), 0) for n in range((h.end - h.start).days + 1))
        for h in holidays
    }


def with_pages(session: Session, cfg: Settings, today: date) -> list[tuple[Holiday, int]]:
    """Upcoming holidays with enough events for a page, and their event counts."""
    holidays = upcoming(cfg, today)
    n = counts(session, holidays, cfg)
    return [(h, n[h.key]) for h in holidays if n[h.key] >= cfg.holiday_pages.min_events]
