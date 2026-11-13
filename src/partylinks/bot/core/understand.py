"""A chat message -> what to do with it. Search itself is the site's (``search.smart_filters``): this
module only takes out what is chat, not search ("מה יש", "תמצא לי", "please"), finds the bot's own
commands (more, near me, cheapest, help), and turns a follow-up ("ומחר?", "and in Haifa?") into the
previous search with that one part replaced."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

from partylinks import countries, seo
from partylinks.config import Settings
from partylinks.search import (
    ABROAD,
    DAYS,
    FILLER,
    FREE,
    HE_PREFIXES,
    PRICE,
    REGION_ALIASES,
    WEEKEND,
    _vocabulary,
    date_ranges,
    exact_dates,
)

# Emoji and punctuation; keeps ג'אז, סופ"ש, 40+, עד 100 ₪, and a date's dot or slash ("7.11", "7/11").
_NOT_TEXT = re.compile(r"(?!(?<=\d)[./](?=\d))[^\w\s'\"״׳₪+<\-]")


@dataclass(frozen=True)
class Message:
    """What a message asks: ``kind`` is search / more / near / help / greeting / thanks / alerts / empty.
    For a search: ``query`` is the words to search (may be empty: "near me" alone), ``cheap`` sorts by
    price, ``near`` asks for (or uses) the chat's location, ``follow_up`` built it on the last search."""

    kind: str
    query: str = ""
    cheap: bool = False
    near: bool = False
    follow_up: bool = False


def lang_of(text: str, fallback: str) -> str:
    """he for Hebrew letters, en for Latin ones, else the chat's language (digits, emoji)."""
    if re.search(r"[֐-׿]", text):
        return "he"
    if re.search(r"[A-Za-z]", text):
        return "en"
    return fallback


def clean(text: str, max_length: int) -> str:
    """Casefolded, no emoji or punctuation, one space between words, at most ``max_length`` characters."""
    text = _NOT_TEXT.sub(" ", text[:max_length].casefold())
    return " ".join(text.split())


def _he_forms(names: set[str]) -> set[str]:
    out = set()
    for name in names:
        name = name.strip().casefold()
        if len(name) < 2:
            continue
        hebrew = bool(re.match(r"[֐-׿]", name))
        out |= {p + name for p in HE_PREFIXES} if hebrew else {name}
    return out


def _places(cfg: Settings) -> frozenset[str]:
    """Every name of a place the search knows (a city, a region, a country, abroad), with ב / ל / מ."""
    return _place_names(tuple(n for c in cfg.enrichment.cities for n in (c.he, c.en, *c.aliases)))


@lru_cache(maxsize=4)
def _place_names(cities: tuple[str, ...]) -> frozenset[str]:
    names = set(cities)
    for key, (he, _, en) in seo.REGION_NAMES.items():
        names |= {he, en, en.removeprefix("the "), *REGION_ALIASES.get(key, ())}
    names |= {name for name, _ in countries.names()} | {"ישראל", "israel"}
    return frozenset(_he_forms(names) | ABROAD)


@lru_cache(maxsize=4)
def _dates(today: date) -> frozenset[str]:
    return frozenset({*WEEKEND, *DAYS, *date_ranges(today)})


def _found(text: str, phrases: frozenset[str] | set[str] | tuple[str, ...] | list[str]) -> list[str]:
    """The phrases in ``text`` (whole words), longest first, each word counted once."""
    padded, out = f" {text} ", []
    for p in sorted(phrases, key=len, reverse=True):
        if f" {p} " in padded:
            padded = padded.replace(f" {p} ", "  ")
            out.append(p)
    return out


def without(text: str, phrases: list[str]) -> str:
    padded = f" {text} "
    for p in phrases:
        padded = padded.replace(f" {p} ", " ")
    return " ".join(padded.split())


def kinds(text: str, cfg: Settings, today: date) -> dict[str, list[str]]:
    """The parts of a search a follow-up can replace: its dates, places, price and free-entry words."""
    return {
        "date": _found(text, _dates(today)) + list(exact_dates(text, today)),
        "place": _found(text, _places(cfg)),
        "price": [m[0] for m in PRICE.finditer(text)],
        "free": _found(text, FREE),
    }


def _drop_filler(text: str, keep: list[str]) -> str:
    """``text`` without chat words, except inside a phrase the search knows ("in march", "יום שישי")."""
    held: dict[str, str] = {}
    for i, phrase in enumerate(sorted(keep, key=len, reverse=True)):
        if " " in phrase and f" {phrase} " in f" {text} ":
            token = f"\x00{i}\x00"
            held[token] = phrase
            text = f" {text} ".replace(f" {phrase} ", f" {token} ").strip()
    words = [w for w in text.split() if w not in FILLER]
    return " ".join(held.get(w, w) for w in words)


def today_in(cfg: Settings) -> date:
    return datetime.now(ZoneInfo(cfg.timezone)).date()


def search_part(text: str, cfg: Settings, today: date) -> tuple[str, bool, bool]:
    """(words to search, cheapest first, near me) of a cleaned message."""
    bot = cfg.bot
    near, cheap = bool(_found(text, bot.near_words)), bool(_found(text, bot.cheap_words))
    text = without(text, _found(text, (*bot.near_words, *bot.cheap_words)))
    parts = kinds(text, cfg, today)
    names = [n for n in _vocabulary(cfg) if " " in n]  # "drum and bass", "stand up": names, not chat words
    return _drop_filler(text, parts["date"] + parts["place"] + names), cheap, near


def follow_up(text: str, previous: str, cfg: Settings, today: date) -> str | None:
    """The previous search with the part this message names replaced ("ומחר", "and in haifa", "בצפון?"),
    or None when the message is a new search."""
    core, marked = text, False
    for marker in cfg.bot.follow_markers:
        if text.startswith(marker):
            core, marked = text.removeprefix(marker), True
            break
    if not marked and text.startswith("ו") and len(text) > 2:
        core = text[1:]  # "ומחר", "ובחיפה": a Hebrew "and" is a letter, a follow-up only if a part follows
    named = kinds(core, cfg, today)
    parts = [p for found in named.values() for p in found]
    if not parts:
        return None
    rest = _drop_filler(without(core, parts), [])
    if rest and not marked:
        return None  # "ויני ויצ'י בחיפה" is a search of its own, not "and in Haifa"
    before = kinds(previous, cfg, today)
    replaced = without(previous, [p for kind, found in named.items() if found for p in before[kind]])
    return " ".join(f"{replaced} {_drop_filler(core, parts)}".split())


def read(text: str, cfg: Settings, previous: str | None = None, today: date | None = None) -> Message:
    """One message of the chat. ``previous``: the chat's recent search, for follow-ups."""
    today = today or today_in(cfg)
    text = clean(text, cfg.bot.max_text_length)
    if not text:
        return Message("empty")
    bot = cfg.bot
    for kind, words in (("greeting", bot.greeting_words), ("thanks", bot.thanks_words), ("help", bot.help_words),
                        ("more", bot.more_words), ("alerts", bot.alerts_words)):  # fmt: skip
        if text in words:
            return Message(kind)
    query, cheap, near = search_part(text, cfg, today)
    if previous is not None and (merged := follow_up(text, previous, cfg, today)) is not None:
        merged, cheap2, near2 = search_part(merged, cfg, today)
        return Message("search", merged, cheap or cheap2, near or near2, follow_up=True)
    if not query and near:
        return Message("near", cheap=cheap)
    if not query and cheap and previous is not None:
        return Message("search", previous, cheap=True, follow_up=True)  # "הכי זול" after a search: re-sorted
    if not query and not (cheap or near):
        return Message("greeting")  # only chat words ("מה יש?"): the menu, not every event
    return Message("search", query, cheap, near)
