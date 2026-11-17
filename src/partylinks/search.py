"""Smart search: the words people type ("טכנו בתל אביב עד 100", "free jazz berlin this weekend") become
filters. Known names (cities, regions, countries, genres) match their events and the text; price and
date words become price / date filters; every other word must appear in the event's text fields.
Searches every event, in Israel and abroad."""

from __future__ import annotations

import re
import warnings
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta
from difflib import get_close_matches
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

from convertdate import hebrew, holidays
from sqlalchemy import (
    ColumnElement,
    SQLColumnExpression,
    String,
    Text,
    and_,
    any_,
    case,
    cast,
    func,
    literal,
    or_,
    select,
)
from sqlalchemy.orm import aliased

from partylinks import countries, search_index, seo
from partylinks.config import Settings, get_settings
from partylinks.db import _HEBREW_PREFIXES, _TWO_PREFIXES, EventRow, lineup_contains, whole_word
from partylinks.places import city_abroad
from partylinks.search_index import norm

Cond = ColumnElement[bool]
_listing = aliased(EventRow)  # another listing of the same event (its group), for ticket-site names
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

# Chat words that are not what an event's text holds ("בא לי", "find me"): left in, each would have to match and
# find nothing. Dropped from the free words only, after known names and dates are taken.
FILLER = {
    "מה", "יש", "לי", "לנו", "תמצא", "תמצאי", "תחפש", "תחפשי", "תביא", "תביאי", "תראה", "תראי", "תן", "תני",
    "אני", "אנחנו", "רוצה", "רוצים", "מחפש", "מחפשת", "מחפשים", "בא", "באלי", "משהו", "איזה", "איזשהו",
    "אירוע", "אירועים", "דברים", "של", "את", "גם", "אולי", "בבקשה", "פליז", "טוב", "טובים", "טובה", "כל", "הכל",
    "איפה", "מתי", "לצאת", "לבלות", "בילוי", "בילויים", "שווה", "שווים", "מומלץ", "מומלצים", "כיף", "כיפי", "נחמד",
    "אפשר", "צריך", "יודע", "תגיד", "תגידי", "ומה", "עם", "קורה", "הולך", "ללכת", "נלך", "לאן",
    "please", "pls", "what", "whats", "what's", "is", "are", "there", "any", "anything", "some", "something",
    "find", "me", "show", "i", "want", "wanna", "looking", "for", "events", "event", "things", "to", "do", "go",
    "on", "the", "a", "an", "at", "good", "best", "fun", "happening", "can", "you", "get", "us", "we", "and",
    "about", "how", "in", "of", "with", "this", "near",
    # "ביום שישי הקרוב", "מסיבה עד 100 שקל", "מה יש לעשות": words around a date, price or wish
    "הקרוב", "הקרובה", "הקרובים", "הקרובות", "הזה", "הזו", "הזאת", "coming", "upcoming", "next",
    "שקל", "שקלים", "לעשות", "לראות", "לשמוע", "שאפשר", "מעניין", "מעניינים", "פעילות", "פעילויות", "activity", "activities",
}  # fmt: skip

HOLIDAY_NAMES = {
    "hanukkah": ("חנוכה", "hanukkah", "chanukah", "hanukah"), "purim": ("פורים", "purim"),
    "passover": ("פסח", "passover", "pesach"), "shavuot": ("שבועות", "shavuot"),
    "rosh_hashanah": ("ראש השנה", "rosh hashanah"), "sukkot": ("סוכות", "sukkot"),
    "tu_bishvat": ("ט״ו בשבט", 'ט"ו בשבט', "טו בשבט", "tu bishvat"),
    "lag_baomer": ("ל״ג בעומר", 'ל"ג בעומר', "לג בעומר", "lag baomer"),
    "tu_bav": ("ט״ו באב", 'ט"ו באב', "טו באב", "tu bav"), "independence_day": ("יום העצמאות", "עצמאות", "independence day"),
    "halloween": ("הלואין", "האלווין", "הלווין", "halloween"), "new_year": ("סילבסטר", "new year", "new years eve"),
    "valentines": ("ולנטיין", "יום האהבה", "valentine", "valentines"),
}  # fmt: skip
# Same dates every year: (month, day) from, to; parties run in the days before.
YEARLY_DATES = {"halloween": ((10, 25), (10, 31)), "new_year": ((12, 28), (1, 1)), "valentines": ((2, 10), (2, 14))}
# Bare "מאי", "יוני", "יולי" are names too: only "במאי", "ביוני", "חודש יולי".
MONTHS = {
    "ינואר": 1, "פברואר": 2, "מרץ": 3, "מרס": 3, "אפריל": 4, "מאי": 5, "יוני": 6, "יולי": 7, "אוגוסט": 8,
    "ספטמבר": 9, "אוקטובר": 10, "נובמבר": 11, "דצמבר": 12,
}  # fmt: skip
BARE_MONTHS = {"מאי", "יוני", "יולי"}
# "march" and "may" are words: only "in march", "in may".
EN_MONTHS = {
    "january": 1, "february": 2, "april": 4, "june": 6, "july": 7, "august": 8, "september": 9,
    "october": 10, "november": 11, "december": 12, "in march": 3, "in may": 5,
}  # fmt: skip
# Python's weekday() (Monday 0). A bare "ראשון" is also ראשון לציון: only "יום ראשון" / "ביום ראשון".
WEEKDAYS = {"ראשון": 6, "שני": 0, "שלישי": 1, "רביעי": 2, "חמישי": 3, "שישי": 4, "שבת": 5}
EN_WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}


def _month_end(d: date) -> date:
    return (d.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def _next_month(today: date, month: int) -> tuple[date, date]:
    start = date(today.year + (month < today.month), month, 1)
    return max(start, today), _month_end(start)


def _next_weekday(today: date, weekday: int) -> tuple[date, date]:
    d = today + timedelta(days=(weekday - today.weekday()) % 7)
    return d, d


def _holiday(year: int, key: str) -> tuple[date, date]:
    """A holiday in Israel in a Gregorian year, eve to last day."""
    if key in YEARLY_DATES:
        (m1, d1), (m2, d2) = YEARLY_DATES[key]
        return date(year, m1, d1), date(year + ((m2, d2) < (m1, d1)), m2, d2)
    day = timedelta(days=1)
    if key == "tu_bav":
        d = date(*hebrew.to_gregorian(year + 3760, hebrew.AV, 15))
        return d - day, d
    if key == "independence_day":  # 5 Iyar, moved off Friday / Saturday / Monday
        d = date(*hebrew.to_gregorian(year + 3760, hebrew.IYYAR, 5))
        d += {4: -day, 5: -2 * day, 0: day}.get(d.weekday(), 0 * day)
        return d - day, d
    d = date(*getattr(holidays, {"tu_bishvat": "tu_beshvat"}.get(key, key))(year))
    last = {"hanukkah": 7, "passover": 6, "sukkot": 7, "rosh_hashanah": 1, "purim": 1}.get(key, 0)
    first = 4 if key == "purim" else 0 if key == "tu_bishvat" else 1  # Purim parties run the days before
    return d - first * day, d + last * day


def _next_holiday(today: date, key: str) -> tuple[date, date]:
    for year in (today.year - 1, today.year, today.year + 1):
        start, end = _holiday(year, key)
        if end >= today:
            return max(start, today), end
    raise AssertionError(key)


# A day by number, Israeli order (day first): "7.11", "ב-7/11", "7.11.26", "07/11/2026".
EXACT_DATE = re.compile(r"(?<!\S)(?:[בלמ]-?)?(\d{1,2})[./](\d{1,2})(?:[./](\d{4}|\d{2}))?(?!\S)")


def exact_dates(text: str, today: date) -> dict[str, date]:
    """Each "7.11"-style phrase in ``text`` -> its date; no year: the next one (a date passed this year is next year's)."""
    out = {}
    for m in EXACT_DATE.finditer(text):
        day, month, year = int(m[1]), int(m[2]), m[3]
        y = (2000 + int(year) if len(year) == 2 else int(year)) if year else today.year
        try:
            d = date(y, month, day)
            if not year and d < today:
                d = date(y + 1, month, day)
        except ValueError:
            continue  # not a date ("40.5", "31.2"): left as a word
        out[m[0]] = d
    return out


@lru_cache(maxsize=4)
def date_ranges(today: date) -> dict[str, tuple[date, date]]:
    """Search phrase -> the dates it means from ``today``: "השבוע", "בדצמבר", "ביום שישי", "חנוכה"."""
    week = (today, today + timedelta(days=6))
    sunday = today + timedelta(days=(6 - today.weekday()) % 7 or 7)
    next_week = (sunday, sunday + timedelta(days=6))
    month = (today, _month_end(today))
    after = _month_end(today) + timedelta(days=1)
    next_month = (after, _month_end(after))
    out = {
        "מחרתיים": (today + timedelta(days=2), today + timedelta(days=2)),
        "השבוע": week, "this week": week, "שבוע הבא": next_week, "בשבוע הבא": next_week, "next week": next_week,
        "החודש": month, "this month": month, "חודש הבא": next_month, "בחודש הבא": next_month, "next month": next_month,
    }  # fmt: skip
    for name, m in MONTHS.items():
        for phrase in ("ב" + name, "חודש " + name, *(() if name in BARE_MONTHS else (name,))):
            out[phrase] = _next_month(today, m)
    for name, m in EN_MONTHS.items():
        out[name] = _next_month(today, m)
    for name, weekday in WEEKDAYS.items():
        for phrase in ("יום " + name, "ביום " + name, *(("ב" + name,) if weekday in (3, 4, 5) else ())):
            out[phrase] = _next_weekday(today, weekday)
    for name, weekday in EN_WEEKDAYS.items():
        out[name] = _next_weekday(today, weekday)
    for key, names in HOLIDAY_NAMES.items():
        with warnings.catch_warnings():  # convertdate 2.5.1 calls its own deprecated month_days()
            warnings.filterwarnings("ignore", "month_days is deprecated", DeprecationWarning)
            span = _next_holiday(today, key)
        for name in names:
            out[name] = span
            if re.match(r"[֐-׿]", name):
                out["ב" + name] = span
    return out


def _escape(word: str) -> str:
    return word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _pattern(word: str) -> str:
    return "%" + _escape(word) + "%"


# search.* in Settings: did_you_mean_cutoff is looser than typo_cutoff (a suggestion to click, not extra
# results); typo_cutoff 0.85 is one letter off in a 5-6 letter word; city_min_population: smaller towns abroad
# share names with plain words; short_word_chars: "ראפ" is not in "תראפיה".


def text_match(word: str, whole: bool = False) -> Cond:
    """``word`` in an event's text. ``whole``: a known name (genre, city, ticket site) counts only as a whole
    word, maybe behind a Hebrew prefix: "טכנו" / "techno" is not in "טכנולוגית" / "technology".

    With the full-text index (``search_index``): each word of ``word`` as the start of a word of the text
    ("עומ" finds "עומר", "infect" finds "Infected"), behind Hebrew prefix letters too, finals / niqqud /
    geresh / case / accents ignored; ``whole`` (and a word of one or two letters): whole words only."""
    if search_index.ready():
        return search_index.matches(word, prefix=not whole and len(word) > 2)
    pattern = _pattern(word)

    def found(c: SQLColumnExpression[Any], exact: bool) -> Cond:
        inside = c.ilike(pattern, escape="\\")  # the cheap test first; the regex only checks what it finds
        return and_(inside, whole_word(c, word)) if exact else inside

    exact = whole or len(word) <= get_settings().search.short_word_chars
    # Lineup and the site's own categories (JSONB text) too: a ticket site files tennis under "טניס".
    lists = (cast(EventRow.lineup, String), cast(EventRow.event_types, String), cast(EventRow.source_genres, String))
    return or_(*(found(c, whole) for c in lists), *(found(c, exact) for c in (
        EventRow.title, EventRow.description, EventRow.venue_name, EventRow.city, EventRow.address, EventRow.organizer,
    )))  # fmt: skip


def _whole_word(column: SQLColumnExpression[Any], word: str) -> Cond:
    w = _escape(word)
    return or_(*(column.ilike(p, escape="\\") for p in (w, f"{w} %", f"% {w}", f"% {w} %", f"%-{w}%", f"%{w}-%")))


_HE_SOUNDS = {
    "ג'": "j", "ג׳": "j", "ז'": "z", "ז׳": "z", "צ'": "ts", "צ׳": "ts", "וו": "b",
    "ב": "b", "ג": "g", "ד": "d", "ז": "z", "ח": "k", "ט": "t", "כ": "k", "ך": "k", "ל": "l", "מ": "m", "ם": "m",
    "נ": "n", "ן": "n", "ס": "s", "פ": "p", "ף": "p", "צ": "ts", "ץ": "ts", "ק": "k", "ר": "r", "ש": "s", "ת": "t",
}  # fmt: skip
_LATIN_SOUNDS = [
    (r"tion", "sn"), (r"sh|sch", "s"), (r"ch|kh|ck|qu?|c(?![eiy])", "k"), (r"c", "s"), (r"ph|f", "p"), (r"th", "t"), (r"tz", "ts"),
    (r"x", "ks"), (r"w|v", "b"), (r"[aeiouyh]", ""),
]  # fmt: skip


def skeleton(name: str) -> str:
    """The consonants of a name in one alphabet, so a Hebrew and a Latin spelling meet:
    "אינפקטד מאשרום" and "Infected Mushroom" are both "npktdmsrm". Vowels (and א ה ו י ע) drop out."""
    text = re.sub(r"(?<![א-ת])ו", "b", name.casefold())  # a leading ו is a V ("ויני ויצ'י")
    for he, sound in _HE_SOUNDS.items():
        text = text.replace(he, sound)
    for pattern, sound in _LATIN_SOUNDS:
        text = re.sub(pattern, sound, text)
    return re.sub(r"(.)\1+", r"\1", re.sub(r"[^a-z]", "", text))


def sounds_like(q: str, by_skeleton: dict[str, list[str]]) -> list[str]:
    """Lineup and venue names spelled differently from ``q`` that sound the same ("אינפקטד מאשרום": Infected
    Mushroom, "בארבי": Barby). ponytail: a short skeleton (three consonants) counts only when few names
    have it (a venue's two spellings); fuzzier matching (one consonant off) when the misses log shows a need."""
    key = skeleton(q)
    names = by_skeleton.get(key, []) if len(key) >= 3 else []
    if len(key) == 3 and len({n.casefold() for n in names}) > 3:
        return []
    return [n for n in names if n.casefold() != q.strip().casefold()]


def did_you_mean(q: str, cfg: Settings, names: Iterable[str]) -> str | None:
    """The known name (genre, kind, city, region, ticket site, artist) closest to a search that
    found nothing: "אינפקטד מושרום" -> "Infected Mushroom"."""
    text = " ".join(q.casefold().split())
    choices = {n.casefold(): n for n in (*_vocabulary(cfg), *names) if len(n) >= 3}
    near = get_close_matches(text, list(choices), n=1, cutoff=cfg.search.did_you_mean_cutoff)
    return choices[near[0]] if near and near[0] != text else None


def by_skeleton(names: Iterable[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for name in {n.strip() for n in names if n and n.strip()}:
        out.setdefault(skeleton(name), []).append(name)
    return out


class NameIndex:
    """The stored events' names, for what SQL cannot do: lineup and venue names by skeleton (``sounds_like``:
    "בארבי" is Barby), and every word of the titles and names by first letter (``correct_spelling``)."""

    def __init__(self, by_skeleton: dict[str, list[str]], words: set[str]) -> None:
        self.by_skeleton, self.words = by_skeleton, words
        self.by_letter: dict[str, list[str]] = {}
        for w in sorted(words):
            self.by_letter.setdefault(w[0], []).append(w)

    @classmethod
    def of(cls, rows: Iterable[tuple[list[str] | None, str | None, str | None]]) -> NameIndex:
        """From (lineup, venue_name, title) rows."""
        names: set[str] = set()
        words: set[str] = set()
        for lineup, venue, title in rows:
            names.update(n for n in (*(lineup or ()), venue) if n)
            words.update(norm(title).split())
        for name in names:
            words.update(norm(name).split())
        return cls(by_skeleton(names), {w for w in words if len(w) >= 3 and not w.isdigit()})


def _starts(word: str, typed: str) -> bool:
    """``typed`` is the start of ``word``, either maybe behind a Hebrew prefix letter ("בבארבי", "בארבי")."""
    return (
        word.startswith(typed)
        or (word[0] in "בלמהו" and len(word) > len(typed) + 1 and word[1:].startswith(typed))
        or (typed[0] in "בלמהו" and len(typed) > 3 and word.startswith(typed[1:]))
    )


class SuggestIndex:
    """Artist and venue names (the /categories?anywhere=true lists) for the search box's suggestions while
    typing, matched in memory: the name itself, its start, the start of each of its words (behind Hebrew
    prefix letters too), anywhere inside, or (for a name of 4+ consonants) its sound in the other alphabet
    ("omer adam" suggests "עומר אדם"). Busiest names first within each kind of match."""

    def __init__(self, lists: dict[str, list[dict[str, Any]]]) -> None:
        self.lists = {
            kind: [(norm(x["value"]), skeleton(x["value"]), x) for x in items if x.get("value")]
            for kind, items in lists.items()
        }

    @staticmethod
    def _score(q: str, words: list[str], sound: str, name: str, name_sound: str) -> int | None:
        if name == q:
            return 0
        if name.startswith(q):
            return 1
        name_words = name.split()
        if all(any(_starts(w, x) for w in name_words) for x in words):
            return 2
        if q in name:
            return 3
        if len(sound) >= 4 and name_sound.startswith(sound):
            return 4
        return None

    def find(self, q: str, limit: int) -> dict[str, list[dict[str, Any]]]:
        text = norm(q)
        if len(text) < 2:
            return {kind: [] for kind in self.lists}
        words, sound = text.split(), skeleton(q)
        out = {}
        for kind, items in self.lists.items():
            scored = [
                (score, -int(x.get("events") or 0), x["value"], x)
                for name, name_sound, x in items
                if (score := self._score(text, words, sound, name, name_sound)) is not None
            ]
            out[kind] = [x for *_, x in sorted(scored, key=lambda s: s[:3])[:limit]]
        return out


def correct_spelling(q: str, index: NameIndex, cutoff: float) -> str | None:
    """``q`` with each word no title or name has replaced by the closest one that has ("omer adm" ->
    "omer adam", "infekted mushrom" -> "infected mushroom"); None when no word changes. For a search that
    found nothing: a word compared with the stored words of its first letter and a similar length."""
    out, changed = [], False
    for w in norm(q).split():
        if len(w) >= 3 and not w.isdigit() and w not in index.words:
            near = get_close_matches(
                w, [c for c in index.by_letter.get(w[0], ()) if abs(len(c) - len(w)) <= 2], n=1, cutoff=cutoff
            )
            if near:
                w, changed = near[0], True
        out.append(w)
    return " ".join(out) if changed else None


TIER = 1000  # relevance(): tier * TIER - parts matched


def tier(rank: int) -> int:
    """0 / 1 / 2 of a ``relevance`` value (2: the words are only in the description, address...)."""
    return (rank + TIER - 1) // TIER


def _spaced(column: SQLColumnExpression[Any]) -> ColumnElement[str]:
    """``column`` normalised as the search words are (``search_index.norm``), a space at each end."""
    return literal(" ") + func.spotx_norm(cast(column, Text)) + literal(" ")


def _has_word(spaced: ColumnElement[str], word: str) -> Cond:
    """A whole word of a ``_spaced`` text, maybe behind Hebrew prefix letters ("בבארבי" has "בארבי")."""
    if re.match(r"[֐-׿]", word):
        return spaced.regexp_match(f" (?:{_TWO_PREFIXES}|[{_HEBREW_PREFIXES}])?{re.escape(word)} ")
    return spaced.ilike(f"% {_escape(word)} %", escape="\\")


def ranked_columns(*where: Cond) -> Any:
    """The columns ``relevance`` reads, of the events matching ``where``, the normalised texts computed once a
    row (OFFSET 0 keeps PostgreSQL from folding them back into each of the rank's dozens of tests)."""
    return (
        select(
            EventRow.id, EventRow.genres, EventRow.country, EventRow.start_at,
            _spaced(EventRow.title).label("title"), _spaced(EventRow.lineup).label("lineup"),
            _spaced(EventRow.venue_name).label("venue"),
        )
        .where(*where)
        .offset(0)
        .subquery()
    )  # fmt: skip


def relevance(q: str, cfg: Settings, artists: Iterable[str] = (), cols: Any = None) -> ColumnElement[int] | None:
    """Tier 0: the event is of a searched genre / kind ("טכנו", "מסיבה"), a searched word is a whole word of
    its title, the whole search is a name of its lineup or venue, or a sound-alike name is; 1: a word is
    inside the title, lineup or venue; 2: only elsewhere (description, city...).
    Within a tier, events matching more of the search come first: for "סרט לילדים" a children's film
    before a kids' workshop that only mentions film; the search's words together ("omer adam") in the
    title, lineup or venue count more, and more again at the title's start. Sorts search results before the
    date, so "טכנו" lists techno parties before "טכנודע". Text is compared normalised (``search_index.norm``:
    "Omer Adam," is "omer adam"). The value is tier * TIER - parts matched (smaller first). ``cols``: the
    columns of ``ranked_columns`` (each text normalised once a row), else the event's own."""
    text = " " + " ".join(q.casefold().split()) + " "
    for name in sorted(
        _area_and_site_names(cfg), key=len, reverse=True
    ):  # "גוש דן", "בתל אביב": places, not title words
        text = text.replace(f" {name} ", " ")
    # Dates, prices and "free" are filters, not title words ("הלילה" is no reason to list "מטבח לילה" first).
    today = datetime.now(ZoneInfo(cfg.timezone)).date()
    text = " " + " ".join(PRICE.sub(" ", text).split()) + " "
    for name in sorted(
        {*FREE, *WEEKEND, *DAYS, *date_ranges(today), *exact_dates(text.strip(), today)}, key=len, reverse=True
    ):
        text = text.replace(f" {name.casefold()} ", " ")
    words = [
        w for w in norm(text).split() if len(w) >= 2 and not w.isdigit() and w not in FILLER
    ]  # "מה יש" is in "ישראל"
    artists = list(artists)
    if not words and not artists:
        return None
    if cols is None:
        title, lineup, venue, genre_list = (
            _spaced(EventRow.title),
            _spaced(EventRow.lineup),
            _spaced(EventRow.venue_name),
            EventRow.genres,
        )
    else:
        title, lineup, venue, genre_list = cols.c.title, cols.c.lineup, cols.c.venue, cols.c.genres
    names = (title, lineup, venue)
    genre = lambda g: _genre(cfg, g, genre_list)
    genres = {g for name, gs in _genre_names(cfg).items() if f" {name} " in text for g in gs}
    forms = [f for w in words for f in ((w, w[1:]) if len(w) > 3 and w[0] in "בלמהו" else (w,))]
    phrase = " ".join(words)
    together = or_(*(_has_word(n, phrase) for n in names)) if len(words) > 1 else None
    top = [_has_word(title, f) for f in forms] + [genre(g) for g in genres]
    top += [or_(*(_has_word(n, norm(a)) for n in names)) for a in artists if norm(a)]
    if words:
        top.append(or_(_has_word(lineup, phrase), _has_word(venue, phrase)))
    inside = [or_(*(n.ilike(_pattern(f), escape="\\") for n in names)) for f in forms]
    # Each kind searched and each other word: one part. Words of a kind's own name count with the kind.
    named = {name for name in _genre_names(cfg) if f" {name} " in text}
    kinds = [or_(*(genre(g) for g in _genre_names(cfg)[name])) for name in named]
    loose = [w for w in words if not any(w in norm(name).split() for name in named)]
    parts = kinds + [or_(*(_has_word(title, f) for f in (w, w[1:]) if len(f) >= 2)) for w in loose]  # the title's words
    matched = sum((case((p, 1), else_=0) for p in parts), literal(0)) if parts else literal(0)
    if together is not None:  # "omer adam" together, then at the title's start: before a title with "Adam" alone
        matched = (
            matched
            + case((together, 2), else_=0)
            + case((title.ilike(f" {_escape(phrase)} %", escape="\\"), 1), else_=0)
        )
    if kinds:  # a listing filed under many kinds ("אירועי נובמבר": 9 genres) is less likely the kind asked for
        matched = matched - case((func.jsonb_array_length(genre_list) > cfg.search.umbrella_genres, 1), else_=0)
    # Only artists left ("chillz" is a site name, dropped above): no words, so no "inside" WHEN (an empty or_ is "WHEN  THEN").
    whens = [(or_(*conds), tier) for conds, tier in ((top, 0), (inside, TIER)) if conds]
    return case(*whens, else_=2 * TIER) - matched


def _area_and_site_names(cfg: Settings) -> set[str]:
    names = {
        n for key, (he, _, en) in seo.REGION_NAMES.items() for n in (he, en, *cfg.search.region_aliases.get(key, ()))
    }
    names |= {n for key in cfg.sources for n in (key, *cfg.search.source_aliases.get(key, ()))}
    # A city in Israel too: the search already keeps only its events, so "תל אביב" in a title is no merit.
    names |= {n for c in cfg.enrichment.cities for n in (c.he, c.en, *c.aliases)}
    return {p + n.casefold() for n in names for p in (HE_PREFIXES if re.match(r"[֐-׿]", n) else ("",))}


def _genre(cfg: Settings, genre: str, column: Any = None) -> Cond:
    # JSONB containment: served by the genres GIN index (search_index), so a genre word ORed with text stays indexed.
    column = EventRow.genres if column is None else column
    return or_(*(column.contains([g]) for g in cfg.enrichment.expand_genres([genre])))


def _genre_names(cfg: Settings) -> dict[str, tuple[str, ...]]:
    """Casefolded name (Hebrew / English label, key, synonym) -> the genres it means."""
    names: dict[str, tuple[str, ...]] = {}
    for g in {*cfg.enrichment.genres, *seo.GENRES_HE}:
        for label in (seo.genre_label(g, "he"), seo.genre_label(g, "en"), g.replace("_", " ")):
            for part in {label, *label.split(" / "), *label.split(" ו")}:
                if len(part.strip()) >= 2:
                    names.setdefault(part.strip().casefold(), (g,))
    synonyms: dict[str, tuple[str, ...]] = {}
    for g, words in cfg.search.synonyms.items():  # a word under several genres means all of them
        for w in words:
            synonyms[w.casefold()] = (*synonyms.get(w.casefold(), ()), g)
    for name, gs in synonyms.items():
        names.setdefault(name, gs)
    for name in list(names):  # ג׳אז and ג'אז: either apostrophe
        names.setdefault(name.replace("׳", "'"), names[name])
        names.setdefault(name.replace("'", "׳"), names[name])
    return names


def _countries() -> list[tuple[str, str]]:
    """(name, code) in Hebrew and English: the app's one country index (partylinks.countries)."""
    return countries.names()


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
        for name in (he, en, en.removeprefix("the "), *cfg.search.region_aliases.get(key, ())):
            add(name, lambda key=key: EventRow.area == key)
    for name, genres in _genre_names(cfg).items():
        add(name, lambda genres=genres: or_(*(_genre(cfg, g) for g in genres)))
    for key in cfg.sources:  # a ticket site: everything it sells ("zygo", "גו אאוט")
        for name in (key, *cfg.search.source_aliases.get(key, ())):
            add(name, lambda key=key: _sold_on(key))
    return names


def smart_filters(q: str, cfg: Settings, artists: Iterable[str] = ()) -> list[Cond]:
    """``artists``: lineup names that sound like ``q`` (``sounds_like``); their events match too."""
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
    exact = exact_dates(text.strip(), today)
    for phrase in take(exact):
        filters.append(EventRow.local_date == exact[phrase])
    ranges = date_ranges(today)
    for phrase in take(ranges):
        filters.append(EventRow.local_date.between(*ranges[phrase]))
    vocab = _vocabulary(cfg)
    for name in take(vocab):
        filters.append(
            or_(vocab[name](), text_match(name, whole=True))
        )  # the name in the text counts too: "Jordan" the DJ
    # A city abroad in either language finds it in both: "ברצלונה" also finds "Barcelona" (GeoNames names).
    words = text.split()
    for size in (3, 2, 1):
        for i in range(len(words) - size + 1):
            phrase = " ".join(words[i : i + size])
            if f" {phrase} " not in text:
                continue  # part of a longer city already taken
            for name in (phrase, phrase[1:]) if phrase[0] in "בלמהו" else (phrase,):
                if len(name) >= 3 and (city := city_abroad(name, min_population=cfg.search.city_min_population)):
                    text = text.replace(f" {phrase} ", " ")
                    filters.append(or_(*(text_match(n.casefold()) for n in city[1])))
                    break
    long_names = [n for n in vocab if len(n) >= 4 and " " not in n]
    for w in (w for w in text.split() if len(w) >= 2 and w not in FILLER):
        # "ברומא" is "רומא" with "in": a Hebrew one-letter prefix may be dropped.
        match = or_(text_match(w), text_match(w[1:])) if len(w) > 3 and w[0] in "בלמהו" else text_match(w)
        # A typo of a known name ("טכנוו", "פסטבל") also finds what the name finds.
        if len(w) >= 4 and (near := get_close_matches(w, long_names, n=1, cutoff=cfg.search.typo_cutoff)):
            match = or_(match, vocab[near[0]](), text_match(near[0], whole=True))
        filters.append(match)
    if not filters:
        return []
    return [or_(and_(*filters), *(_name_match(a) for a in artists))]


def _sold_on(source: str) -> Cond:
    """Events a ticket site sells (any listing of the group): ``= ANY(ARRAY(...))``, computed once, so the
    planner can read it from the group_id index inside an OR (an ``IN (SELECT ...)`` there scans every row)."""
    groups = func.array(
        select(_listing.group_id).where(_listing.source == source, _listing.group_id.is_not(None)).scalar_subquery()
    )
    return EventRow.group_id == any_(groups)


def _name_match(name: str) -> Cond:
    """Events with a sound-alike name (``sounds_like``) in their lineup or venue."""
    if search_index.ready():
        return search_index.matches(name, prefix=False)
    return lineup_contains(name) | EventRow.venue_name.ilike(_pattern(name), escape="\\")
