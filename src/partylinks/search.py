"""Smart search: the words people type ("טכנו בתל אביב עד 100", "free jazz berlin this weekend") become
filters. Known names (cities, regions, countries, genres) match their events and the text; price and
date words become price / date filters; every other word must appear in the event's text fields.
Searches every event, in Israel and abroad."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from functools import cache
from typing import Any
from zoneinfo import ZoneInfo

import pycountry
from sqlalchemy import ColumnElement, String, and_, case, cast, or_, select
from sqlalchemy.orm import aliased

from partylinks import seo
from partylinks.config import Settings
from partylinks.db import EventRow, lineup_contains

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


# Words people search by that name a kind of event rather than a genre label.
PARTY = ("techno", "house", "trance", "mainstream", "hip_hop", "electronic", "nightclubs", "reggaeton_latin", "afro",
         "disco_funk", "drum_and_bass", "bass_dubstep", "hardstyle", "edm", "throwback", "mizrahi", "rnb", "dancehall_reggae", "dark_wave")
SYNONYMS: dict[str, tuple[str, ...]] = {
    "מסיבה": PARTY, "מסיבות": PARTY, "party": PARTY, "parties": PARTY, "ליין": PARTY, "רייב": PARTY, "rave": PARTY,
    "מסיבת טבע": ("trance",), "מסיבות טבע": ("trance",), "פסיכדלי": ("trance",), "psytrance": ("trance",), "פסי טראנס": ("trance",),
    "הופעה": ("live_show",), "הופעות": ("live_show",), "הופעה חיה": ("live_show",), "קונצרט": ("live_show", "classical_opera"),
    "concert": ("live_show",), "live": ("live_show",), "מועדון": ("nightclubs",), "מועדונים": ("nightclubs",), "קלאב": ("nightclubs",),
    "הצגה": ("theatre",), "הצגות": ("theatre",), "תאטרון": ("theatre",), "מחזמר": ("theatre",), "סרט": ("cinema",), "סרטים": ("cinema",),
    "ילדים": ("kids",), "משפחה": ("kids",), "הרצאה": ("lectures",), "סדנה": ("workshops",), "סדנאות": ("workshops",),
    "פסטיבל": ("festival",), "פסטיבלים": ("festival",), "קומדיה": ("stand_up",), "stand up": ("stand_up",), "סטנד אפ": ("stand_up",),
    "ג'אז": ("jazz",), "היפהופ": ("hip_hop",), "hiphop": ("hip_hop",), "ראפ": ("hip_hop",), "rap": ("hip_hop",),
    "גאה": ("lgbt",), "להטב": ("lgbt",), "להט״ב": ("lgbt",), "חדר בריחה": ("escape_rooms",), "חדרי בריחה": ("escape_rooms",),
    "הפי אוור": ("happy_hour",), "יוגה": ("yoga_wellness",), "אופרה": ("classical_opera",), "קלאסית": ("classical_opera",),
    "רוק": ("rock_indie",), "אינדי": ("rock_indie",), "דיסקו": ("disco_funk",), "רגאטון": ("reggaeton_latin",), "לטיני": ("reggaeton_latin",),
}  # fmt: skip
REGION_ALIASES = {
    "gush_dan": ("גוש דן", "מרכז", "המרכז", "center"), "haifa": ("קריות", "הקריות", "krayot"),
    "galilee": ("צפון", "הצפון", "גליל", "הגליל", "north"), "negev": ("דרום", "הדרום", "נגב", "הנגב", "south"),
}  # fmt: skip
SOURCE_ALIASES = {
    "goout": ("go out", "go-out", "גו אאוט", "גואאוט"), "eventer": ("אוונטר", "איוונטר"), "zygo": ("זייגו",),
    "tickchak": ("טיקצ'אק", "טיקצאק"), "smarticket": ("סמרטיקט",), "barby": ("בארבי",), "kupatbravo": ("קופת ברבו", "kupat bravo"),
    "ticketmaster": ("טיקטמאסטר",), "mevalim": ("מבלים",), "hulyo": ("הוליו",), "popit": ("פופיט",), "beatradar": ("beat radar", "ביט ראדר"),
    "happytlv": ("happy tlv", "הפי תל אביב"), "secrettelaviv": ("secret tel aviv",), "cinemacity": ("cinema city", "סינמה סיטי"),
    "hotcinema": ("hot cinema", "הוט סינמה"), "ravhen": ("rav hen", "רב חן"), "lev": ("סינמה לב",), "zappa": ("זאפה",),
}  # fmt: skip


def _escape(word: str) -> str:
    return word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _pattern(word: str) -> str:
    return "%" + _escape(word) + "%"


def _json_has(column: ColumnElement[Any], word: str) -> Cond:
    """A JSON list column (event types) holding ``word``: stored with non-ASCII escaped, so both forms."""
    text = cast(column, String)
    return or_(*(text.ilike(_pattern(f), escape="\\") for f in {word, json.dumps(word)[1:-1]}))


def text_match(word: str) -> Cond:
    pattern = _pattern(word)
    return or_(lineup_contains(word), _json_has(EventRow.event_types, word), *(c.ilike(pattern, escape="\\") for c in (
        EventRow.title, EventRow.description, EventRow.venue_name, EventRow.city, EventRow.address, EventRow.organizer,
    )))  # fmt: skip


def _whole_word(column: ColumnElement[Any], word: str) -> Cond:
    w = _escape(word)
    return or_(*(column.ilike(p, escape="\\") for p in (w, f"{w} %", f"% {w}", f"% {w} %", f"%-{w}%", f"%{w}-%")))


def relevance(q: str, cfg: Settings) -> ColumnElement[int] | None:
    """0: the event is of a searched genre / kind ("טכנו", "מסיבה") or a searched word is a whole word of
    its title; 1: a word is inside the title, lineup or venue; 2: only elsewhere (description, city...).
    Sorts search results before the date, so "טכנו" lists techno parties before "טכנודע"."""
    words = [w for w in re.findall(r"\w{2,}", q.casefold()) if not w.isdigit()]
    if not words:
        return None
    text = " " + " ".join(q.casefold().split()) + " "
    genres = {g for name, gs in _genre_names(cfg).items() if f" {name} " in text for g in gs}
    forms = [f for w in words for f in ((w, w[1:]) if len(w) > 3 and w[0] in "בלמהו" else (w,))]
    top = [_whole_word(EventRow.title, f) for f in forms] + [_genre(cfg, g) for g in genres]
    inside = [
        EventRow.title.ilike(_pattern(f), escape="\\") | lineup_contains(f) | EventRow.venue_name.ilike(_pattern(f), escape="\\")
        for f in forms
    ]
    return case((or_(*top), 0), (or_(*inside), 1), else_=2)


def _genre(cfg: Settings, genre: str) -> Cond:
    return or_(*(cast(EventRow.genres, String).ilike(f'%"{g}"%') for g in cfg.enrichment.expand_genres([genre])))


def _genre_names(cfg: Settings) -> dict[str, tuple[str, ...]]:
    """Casefolded name (Hebrew / English label, key, synonym) -> the genres it means."""
    names: dict[str, tuple[str, ...]] = {}
    for g in {*cfg.enrichment.genres, *seo.GENRES_HE}:
        for label in (seo.genre_label(g, "he"), seo.genre_label(g, "en"), g.replace("_", " ")):
            for part in {label, *label.split(" / "), *label.split(" ו")}:
                if len(part.strip()) >= 2:
                    names.setdefault(part.strip().casefold(), (g,))
    for name, gs in SYNONYMS.items():
        names.setdefault(name.casefold(), gs)
    for name in list(names):  # ג׳אז and ג'אז: either apostrophe
        names.setdefault(name.replace("׳", "'"), names[name])
        names.setdefault(name.replace("'", "׳"), names[name])
    return names


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
        for name in (he, en, en.removeprefix("the "), *REGION_ALIASES.get(key, ())):
            add(name, lambda key=key: EventRow.area == key)
    for name, genres in _genre_names(cfg).items():
        add(name, lambda genres=genres: or_(*(_genre(cfg, g) for g in genres)))
    for key in cfg.sources:  # a ticket site: everything it sells ("zygo", "גו אאוט")
        for name in (key, *SOURCE_ALIASES.get(key, ())):
            add(name, lambda key=key: EventRow.group_id.in_(select(_listing.group_id).where(_listing.source == key)))
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
