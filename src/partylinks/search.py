"""Smart search: the words people type ("טכנו בתל אביב עד 100", "free jazz berlin this weekend") become
filters. Known names (cities, regions, countries, genres) match their events and the text; price and
date words become price / date filters; every other word must appear in the event's text fields.
Searches every event, in Israel and abroad."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from functools import cache
from zoneinfo import ZoneInfo

import pycountry
from sqlalchemy import ColumnElement, String, and_, case, cast, or_

from partylinks import seo
from partylinks.config import Settings
from partylinks.db import EventRow, lineup_contains

Cond = ColumnElement[bool]
HE_PREFIXES = ("", "ב", "ל", "מ", "ה", "ו", "וב")  # "בתל אביב", "לחיפה", "מברלין"
FREE = {"free", "חינם", "בחינם", "כניסה חופשית", "כניסה חינם", "ללא עלות"}
ABROAD = {"abroad", "חול", "חו״ל", 'חו"ל', "בחול", "בחו״ל", 'בחו"ל'}
PRICE = re.compile(r'(?:עד|under|below|max|<)\s*(\d{2,4})|(\d{2,4})\s*(?:₪|ש"ח|ש״ח|שח|nis|ils)', re.IGNORECASE)
DAYS = {
    "tonight": 0, "today": 0, "הערב": 0, "היום": 0, "הלילה": 0,
    "tomorrow": 1, "מחר": 1, "מחר בערב": 1,
}  # fmt: skip
WEEKEND = {
    "weekend",
    "this weekend",
    "סופש",
    "סופ״ש",
    'סופ"ש',
    "בסופש",
    "בסופ״ש",
    'בסופ"ש',
    "סוף שבוע",
    "בסוף השבוע",
    "סוף השבוע",
}


def _pattern(word: str) -> str:
    return "%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def text_match(word: str) -> Cond:
    pattern = _pattern(word)
    return or_(lineup_contains(word), *(c.ilike(pattern, escape="\\") for c in (
        EventRow.title, EventRow.description, EventRow.venue_name, EventRow.city, EventRow.address,
    )))  # fmt: skip


def relevance(q: str) -> ColumnElement[int] | None:
    """0: a searched word is in the title, 1: in the lineup or venue, 2: only elsewhere (description, city).
    Sorts search results before the date, so "טכנו" lists techno parties before a talk that mentions it."""
    words = [w for w in re.findall(r"\w{2,}", q.casefold()) if not w.isdigit()]
    if not words:
        return None

    forms = [f for w in words for f in ((w, w[1:]) if len(w) > 3 and w[0] in "בלמהו" else (w,))]
    return case(
        (or_(*(EventRow.title.ilike(_pattern(f), escape="\\") for f in forms)), 0),
        (or_(*(lineup_contains(f) | EventRow.venue_name.ilike(_pattern(f), escape="\\") for f in forms)), 1),
        else_=2,
    )


def _genre(cfg: Settings, genre: str) -> Cond:
    return or_(*(cast(EventRow.genres, String).ilike(f'%"{g}"%') for g in cfg.enrichment.expand_genres([genre])))


@cache
def _countries() -> list[tuple[str, str]]:
    """(name, code) in Hebrew and English."""
    out = []
    for c in pycountry.countries:
        for name in {seo.country_name(c.alpha_2, "he"), seo.country_name(c.alpha_2, "en"), c.name}:
            out.append((name, c.alpha_2))
    return out


def _vocabulary(cfg: Settings) -> dict[str, Callable[[], Cond]]:
    """Casefolded name -> its filter. Built per call: a few hundred names, cheap next to the query."""
    names: dict[str, Callable[[], Cond]] = {}

    def add(name: str, cond: Callable[[], Cond]) -> None:
        name = name.strip().casefold()
        if len(name) < 2:
            return
        hebrew = bool(re.match(r"[֐-׿]", name))
        for prefix in HE_PREFIXES if hebrew else ("",):
            names.setdefault(prefix + name, cond)

    for c in cfg.enrichment.cities:  # first: a city's name wins over a country or a genre of the same name
        for name in (c.he, c.en, *c.aliases):
            add(name, lambda he=c.he: EventRow.city == he)
    for name, code in _countries():
        add(name, lambda code=code: EventRow.country == code)
    add("ישראל", lambda: EventRow.country.is_(None))
    add("israel", lambda: EventRow.country.is_(None))
    for key, (he, _, en) in seo.REGION_NAMES.items():
        for name in (he, en, en.removeprefix("the ")):
            add(name, lambda key=key: EventRow.area == key)
    for g in cfg.enrichment.genres:
        for label in (seo.genre_label(g, "he"), seo.genre_label(g, "en"), g.replace("_", " ")):
            for part in {label, *label.split(" / ")}:
                add(part, lambda g=g: _genre(cfg, g))
    return names


def smart_filters(q: str, cfg: Settings) -> list[Cond]:
    text = " " + re.sub(r"\s+", " ", q.casefold()) + " "
    filters: list[Cond] = []

    def take(phrases: Iterable[str]) -> list[str]:
        nonlocal text
        found = []
        for p in sorted(phrases, key=len, reverse=True):
            if f" {p} " in text:
                text = text.replace(f" {p} ", " ")
                found.append(p)
        return found

    for m in PRICE.finditer(text):
        filters.append(EventRow.group_min_price <= int(m[1] or m[2]))
    text = PRICE.sub(" ", text)
    if take(FREE):
        filters.append(EventRow.group_has_free_entry.is_(True))
    if take(ABROAD):
        filters.append(EventRow.country.is_not(None))
    today = datetime.now(ZoneInfo(cfg.timezone)).date()
    if take(WEEKEND):
        filters += [EventRow.is_weekend.is_(True), EventRow.local_date <= today + timedelta(days=7)]
    for word in take(DAYS):
        filters.append(EventRow.local_date == today + timedelta(days=DAYS[word]))
    vocab = _vocabulary(cfg)
    for name in take(vocab):
        filters.append(or_(vocab[name](), text_match(name)))  # the name in the text counts too: "Jordan" the DJ
    for w in (w for w in text.split() if len(w) >= 2):
        # "ברומא" is "רומא" with "in": a Hebrew one-letter prefix may be dropped.
        filters.append(or_(text_match(w), text_match(w[1:])) if len(w) > 3 and w[0] in "בלמהו" else text_match(w))
    return [and_(*filters)] if filters else []
