"""The menu: browse like the site's filters, by category (its genre groups, then a group's genres), area and
date. Every button carries the whole choice, so the menu keeps no state:

    m:<screen>:<genre>:<area>:<when>      (at most 64 bytes, Telegram's callback data)

screen: h home, g the category groups, G a group's genres (the group in <genre>), a areas, w dates, r the
results. A choice runs as the site's own search words ("טכנו בצפון בסופ״ש"), so a menu search is a search
like any other: "more", 🔔 alert me, "and tomorrow?" all work on it."""

from __future__ import annotations

from typing import TYPE_CHECKING

from partylinks import seo
from partylinks.bot.core import daily, evening, highlights, webapp
from partylinks.bot.core.phrases import label, say
from partylinks.bot.core.reply import MENU, REFINE, SEARCH, Button, Reply
from partylinks.config import Settings

if TYPE_CHECKING:
    from partylinks.bot.core.conversation import Conversation

HOME = MENU + "h"
# The search words of an area, in each language: names the site's search reads as that area (REGION_NAMES,
# REGION_ALIASES, ABROAD in search.py; tests/test_bot_menu.py checks each).
AREAS = {
    "gush_dan": ("במרכז", "center"), "sharon": ("השרון", "sharon"), "jerusalem": ("בירושלים והסביבה", "jerusalem area"),
    "haifa": ("בחיפה והקריות", "haifa and the krayot"), "galilee": ("בצפון", "north"), "negev": ("בדרום", "south"),
    "abroad": ("בחו״ל", "abroad"),
}  # fmt: skip
AREA_LABELS = {
    "gush_dan": ("🏙 מרכז וגוש דן", "🏙 Center"), "sharon": ("🌳 השרון", "🌳 Sharon"),
    "jerusalem": ("🕍 ירושלים והסביבה", "🕍 Jerusalem area"), "haifa": ("⚓ חיפה והקריות", "⚓ Haifa area"),
    "galilee": ("⛰ הצפון והגליל", "⛰ North"), "negev": ("🏜 הדרום והנגב", "🏜 South"), "abroad": ("✈️ חו״ל", "✈️ Abroad"),
}  # fmt: skip
# The search words and the label of a date choice.
WHEN = {
    "tn": (("הערב", "tonight"), ("🌙 הערב", "🌙 Tonight")),
    "tm": (("מחר", "tomorrow"), ("☀️ מחר", "☀️ Tomorrow")),
    "wk": (("בסופ״ש", "this weekend"), ("🎉 סופ״ש", "🎉 Weekend")),
    "w7": (("השבוע", "this week"), ("📆 השבוע", "📆 This week")),
}  # fmt: skip
GROUP_ICONS = {
    "g_parties": "🪩", "g_live": "🎸", "g_stage": "🎭", "g_culture": "🏛", "g_kids": "🧸", "g_food": "🍷",
    "g_leisure": "🏄", "g_tech": "💻", "g_community": "🏳️‍🌈",
}  # fmt: skip


def _i(lang: str) -> int:
    return 0 if lang == "he" else 1


def genre_label(genre: str, lang: str) -> str:
    return seo.genre_label(genre, lang)


def genre_words(genre: str, lang: str) -> str:
    """Search words for a genre: its label's first name ("רגאטון / לטיני" -> "רגאטון"), which the site's
    search reads as the genre (search._genre_names)."""
    return genre_label(genre, lang).split(" / ")[0]


def query(genre: str, area: str, when: str, lang: str) -> str:
    i = _i(lang)
    parts = [genre_words(genre, lang) if genre else "", AREAS[area][i] if area in AREAS else "", WHEN[when][0][i] if when in WHEN else ""]
    return " ".join(p for p in parts if p)


def action(screen: str, genre: str = "", area: str = "", when: str = "") -> str:
    return f"{MENU}{screen}:{genre}:{area}:{when}"


def parse(data: str) -> tuple[str, str, str, str]:
    parts = (data.removeprefix(MENU).split(":") + ["", "", "", ""])[:4]
    return parts[0], parts[1], parts[2], parts[3]


def _pairs(buttons: list[Button]) -> list[list[Button]]:
    return [buttons[i : i + 2] for i in range(0, len(buttons), 2)]


def _back(lang: str, genre: str, area: str, when: str) -> list[Button]:
    """Back to the results of the choice so far (or home when there is none)."""
    if genre or area or when:
        return [Button(label("menu_show", lang), action("r", genre, area, when)), Button(label("menu_home", lang), HOME)]
    return [Button(label("menu_home", lang), HOME)]


def home(cfg: Settings, lang: str, edit: bool = False) -> Reply:
    i = _i(lang)
    rows = [
        [Button(label("menu_genres", lang), action("g")), Button(label("menu_areas", lang), action("a"))],
        [Button(WHEN["tn"][1][i], action("r", when="tn")), Button(WHEN["tm"][1][i], action("r", when="tm"))],
        [Button(WHEN["wk"][1][i], action("r", when="wk")), Button(label("free", lang), SEARCH + ("חינם" if lang == "he" else "free"))],
        *highlights.rows(cfg, lang),
        [evening.button(lang), daily.button(lang)],
        [Button(label("near", lang), MENU + "near")],
        [Button(label("my_alerts", lang), MENU + "alerts"), Button(label("my_events", lang), MENU + "mine")],
        [Button(label("menu_lang", lang), MENU + "lang")],
        *webapp.rows(cfg, lang),
    ]
    return Reply(say("menu", lang), buttons=rows, edit=edit)


def groups(cfg: Settings, lang: str, area: str, when: str) -> Reply:
    keys = list(cfg.enrichment.genre_groups)
    buttons = [Button(f"{GROUP_ICONS.get(g, '•')} {genre_label(g, lang)}", action("G", g, area, when)) for g in keys]
    return Reply(say("menu_pick_group", lang), buttons=[*_pairs(buttons), _back(lang, "", area, when)], edit=True)


def group(cfg: Settings, lang: str, group_key: str, area: str, when: str) -> Reply:
    members = cfg.enrichment.genre_groups.get(group_key, [])
    every = Button(f"{GROUP_ICONS.get(group_key, '•')} {label('menu_all', lang)} {genre_label(group_key, lang)}", action("r", group_key, area, when))
    buttons = [Button(genre_label(g, lang), action("r", g, area, when)) for g in members]
    rows = [[every], *_pairs(buttons), [Button(label("menu_back", lang), action("g", "", area, when))]]
    return Reply(say("menu_pick_genre", lang, g=genre_label(group_key, lang)), buttons=rows, edit=True)


def areas(lang: str, genre: str, when: str) -> Reply:
    i = _i(lang)
    buttons = [Button(AREA_LABELS[a][i], action("r", genre, a, when)) for a in AREAS]
    rows = [*_pairs(buttons), [Button(label("menu_everywhere", lang), action("r", genre, "", when))], _back(lang, genre, "", when)]
    return Reply(say("menu_pick_area", lang), buttons=rows, edit=True)


def dates(lang: str, genre: str, area: str) -> Reply:
    i = _i(lang)
    buttons = [Button(WHEN[w][1][i], action("r", genre, area, w)) for w in WHEN]
    rows = [*_pairs(buttons), [Button(label("menu_anytime", lang), action("r", genre, area, ""))], _back(lang, genre, area, "")]
    return Reply(say("menu_pick_date", lang), buttons=rows, edit=True)


def bar(lang: str, genre: str, area: str, when: str) -> list[list[Button]]:
    """Under menu results: change one part of the choice, keep the others."""
    i = _i(lang)
    g = f"🎵 {genre_label(genre, lang)}" if genre else label("menu_genres", lang)
    a = AREA_LABELS[area][i] if area in AREA_LABELS else label("menu_areas", lang)
    w = WHEN[when][1][i] if when in WHEN else label("menu_dates", lang)
    return [
        [Button(f"{g} ✏️", action("g", "", area, when)), Button(f"{a} ✏️", action("a", genre, "", when))],
        [Button(f"{w} ✏️", action("w", genre, area, "")), Button(label("menu_home", lang), HOME)],
    ]


async def press(conv: Conversation, chat_id: str, lang: str, data: str) -> Reply:
    cfg = conv.settings()
    if data == MENU + "near":
        return Reply(say("ask_location", lang), ask_location=True)
    if data == MENU + "alerts":
        return await conv.alerts(chat_id, lang)
    if data == MENU + "mine":
        return await conv.reminders(chat_id, lang)
    if data == MENU + "lang":
        other = "en" if lang == "he" else "he"
        await conv.set_lang(chat_id, other)
        reply = home(cfg, other)
        reply.text = say("lang_set", other) + "\n\n" + reply.text
        return reply
    screen, genre, area, when = parse(data)
    if screen == "g":
        return groups(cfg, lang, area, when)
    if screen == "G":
        return group(cfg, lang, genre, area, when)
    if screen == "a":
        return areas(lang, genre, when)
    if screen == "w":
        return dates(lang, genre, area)
    if screen == "r":
        words = query(genre, area, when, lang)
        reply = await conv._search(chat_id, lang, words, False, None)
        # The bar replaces the "tonight / weekend" refine row: it changes the date (and the rest) itself.
        kept = [row for row in reply.buttons if not any(b.action and b.action.startswith(REFINE) for b in row)]
        reply.buttons = [*kept, *bar(lang, genre, area, when)]
        return reply
    return home(cfg, lang, edit=screen == "h")
