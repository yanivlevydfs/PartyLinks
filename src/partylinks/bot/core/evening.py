"""🌙 Plan my evening: an event at the place and date asked (tonight by default) with the site's evening
around it (``evening.plan.plan``: drinks and dinner nearby before it, a bar or late food after), each stop a labelled
card in that order. No place: the chat's last one, else the area buttons. The buttons carry the search and
where in it the evening was found (``ev:<offset>:<words>``): "another evening" is the next event; no state."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from partylinks.bot.core import menu, refs, understand, usage
from partylinks.bot.core.card import cards, when
from partylinks.bot.core.find import search
from partylinks.bot.core.phrases import label
from partylinks.bot.core.reply import MAX_ACTION_BYTES, Button, Reply
from partylinks.config import Settings
from partylinks.db import EventRow
from partylinks.evening.plan import Span, plan

if TYPE_CHECKING:
    from partylinks.bot.core.conversation import Conversation

PREFIX = "ev:"
START = "0:"
Stop = tuple[str, EventRow, int | None, Span | None]  # (step key or "main", listing, meters away, opening hours)
TEXT: dict[str, dict[str, str]] = {
    "button": {"he": "🌙 תכננו לי ערב", "en": "🌙 Plan my evening"},
    "where": {"he": "🌙 איפה נבלה? בחרו אזור:", "en": "🌙 Where to? Pick an area:"},
    "intro": {"he": "🌙 תכננתי לכם ערב ({q}):", "en": "🌙 Here's your evening ({q}):"},
    "main": {"he": "🎉 האירוע:", "en": "🎉 The event:"},
    # The site's steps (evening.steps keys); a step added in the admin gets "before" / "after".
    "drinks": {"he": "🍸 לפני: האפי האוור", "en": "🍸 Before: happy hour"},
    "dinner": {"he": "🍽️ לפני: ארוחה", "en": "🍽️ Before: dinner"},
    "late": {"he": "🍻 אחרי: בר או אוכל בלילה", "en": "🍻 After: a bar or late food"},
    "before": {"he": "⏮ לפני:", "en": "⏮ Before:"},
    "after": {"he": "⏭ אחרי:", "en": "⏭ After:"},
    "m": {"he": "🚶 {n} מ׳ מהאירוע", "en": "🚶 {n} m from the event"},
    "km": {"he": "🚶 {n} ק״מ מהאירוע", "en": "🚶 {n} km from the event"},
    "another": {"he": "🔁 ערב אחר", "en": "🔁 Another evening"},
    "none": {
        "he": "לא הצלחתי לבנות ערב כזה 🤷 אולי משהו מאלה?",
        "en": "I couldn't build that evening 🤷 Maybe one of these?",
    },
}
# Openings of a message (after understand.clean) that ask for an evening; the rest is its place and date.
WORDS = sorted({
    "תכנן לי ערב", "תכנני לי ערב", "תכננו לי ערב", "תתכנן לי ערב", "תתכנני לי ערב", "תכנן ערב", "תכננו ערב",
    "תכנון ערב", "plan me an evening", "plan an evening", "plan my evening", "plan an evening out", "plan my night",
    "plan a night out", "plan me a night out",
}, key=len, reverse=True)  # fmt: skip


def _t(key: str, lang: str, **values: object) -> str:
    return TEXT[key][lang].format(**values)


def _action(offset: int, words: str) -> str | None:
    data = f"{PREFIX}{offset}:{words.strip()}"
    return data if len(data.encode()) <= MAX_ACTION_BYTES else None


def button(lang: str) -> Button:
    """The menu's button."""
    return Button(_t("button", lang), PREFIX + START)


def asked(text: str, max_length: int) -> str | None:
    """A message asking for an evening -> its button data (``0:<the rest>``), else None."""
    text = understand.clean(text, max_length)
    return next((START + text.removeprefix(w).strip() for w in WORDS if f"{text} ".startswith(f"{w} ")), None)


def _where(lang: str, words: str) -> Reply:
    """The area buttons, each keeping the words asked (when they fit)."""
    i = menu._i(lang)
    pick = [
        Button(menu.AREA_LABELS[a][i], _action(0, f"{words} {w[i]}") or _action(0, w[i])) for a, w in menu.AREAS.items()
    ]
    return Reply(_t("where", lang), buttons=[*menu._pairs(pick), [Button(label("menu_home", lang), menu.HOME)]])


def _opening(lines: tuple[str, ...], row: EventRow, opens: Span | None, cfg: Settings, lang: str) -> tuple[str, ...]:
    """An "after" stop shows the place's opening hours (as the site), not its listing's slot: "🗓 הערב · 18:00–02:00"."""
    if not opens or not lines:
        return lines
    end = opens[1].astimezone(ZoneInfo(row.time_zone or cfg.timezone))
    return (f"{when(row, cfg, lang, start=opens[0])}–{end:%H:%M}", *lines[1:])


def _distance(meters: int, lang: str) -> str:
    return _t("m", lang, n=meters) if meters < 1000 else _t("km", lang, n=f"{meters / 1000:.1f}")


async def press(conv: Conversation, chat_id: str, lang: str, data: str) -> Reply:
    """``data``: ``<offset>:<words>`` (without the prefix)."""
    from partylinks.api import build_event_filters  # local: api imports the bot

    cfg = conv.settings()
    today = understand.today_in(cfg)
    offset, _, words = data.partition(":")
    start = int(offset) if offset.isascii() and offset.isdigit() else 0
    words = understand.search_part(words, cfg, today)[0]
    steps = {s.key: s.when for s in cfg.evening.steps}

    def work(session: Session) -> tuple[str, int, list[Stop], dict[str, dict[str, Any]]] | None:
        query = words
        if not understand.kinds(query, cfg, today)["place"]:
            last = understand.kinds(conv._recent(conv._chat(session, chat_id, lang)) or "", cfg, today)["place"]
            if not last:
                return None
            query += " " + " ".join(last)
        if not understand.kinds(query, cfg, today)["date"]:
            query += " " + menu.WHEN["tn"][0][menu._i(lang)]
        query = query.strip()
        found = search(session, cfg, query, offset=start, limit=cfg.bot.evening_tries)
        # The first event with places around it; none has any: the first event alone.
        best: tuple[int, EventRow, list[Stop]] | None = None
        for n, row in enumerate(found.rows):
            if found.extras.get(row.id, {}).get("place"):
                continue
            planned = plan(session, cfg, row, lambda **kw: build_event_filters(cfg, **kw)).steps
            stops = [(s.key, *s.items[0], s.opens.get(s.items[0][0].id)) for s in planned]
            if best is None or stops:
                best = (n, row, stops)
            if stops:
                break
        evening: list[Stop] = []
        if best:
            n, row, stops = best
            evening = [s for s in stops if steps.get(s[0]) == "before"] + [("main", row, None, None)]
            evening += [s for s in stops if steps.get(s[0]) != "before"]
        usage.record(session, conv.channel, chat_id, "evening", q=query, results=len(evening))
        return query, start + (best[0] if best else 0), evening, refs.extras(session, [r for _, r, _, _ in evening])

    result = await conv._in_db(work)
    if result is None:
        return _where(lang, words)
    query, at, evening, extras = result
    if not evening:
        return Reply(_t("none", lang), buttons=conv._starters(lang))
    drawn = await cards([r for _, r, _, _ in evening], extras, cfg, lang)
    shown = [
        replace(c, title=f"{_t(key if key in TEXT else steps.get(key, 'after'), lang)} {c.title}",
                lines=_opening(c.lines, r, opens, cfg, lang) + ((_distance(d, lang),) if d is not None else ()))
        for (key, r, d, opens), c in zip(evening, drawn, strict=True)
    ]  # fmt: skip
    another = _action(at + 1, query)
    row = [Button(_t("another", lang), another)] if another else []
    return Reply(_t("intro", lang, q=query), shown, [[*row, Button(label("menu_home", lang), menu.HOME)]])
