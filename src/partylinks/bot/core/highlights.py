"""🔥 The week's top and 🎊 the coming holidays' events in the menu, as the site picks them (weekly_top,
holiday_pages). A holiday's button shows from ``holiday_pages.rail_days`` before it, like the home row.
"More" carries its offset in the button (``hl:t:<offset>``, ``hl:h:<key>:<offset>``): no state."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from partylinks import seo
from partylinks.bot.core import refs, understand, usage
from partylinks.bot.core.card import cards
from partylinks.bot.core.phrases import label
from partylinks.bot.core.reply import MENU, Button, Reply
from partylinks.config import Settings
from partylinks.db import EventRow
from partylinks.holiday_pages.core import Holiday, upcoming, where
from partylinks.holiday_pages.pages import _dates
from partylinks.weekly_top.core import top

if TYPE_CHECKING:
    from partylinks.bot.core.conversation import Conversation

PREFIX = "hl:"
FIRST_TOP = "t:0"
TEXT: dict[str, dict[str, str]] = {
    "top_button": {"he": "🔥 הכי חם השבוע", "en": "🔥 Top of the week"},
    "holiday_button": {"he": "{icon} אירועי {name}", "en": "{icon} {name} events"},
    "top": {"he": "🔥 הכי מבוקשים השבוע:", "en": "🔥 Most wanted this week:"},
    "holiday": {"he": "{icon} אירועי {name} ({dates}):", "en": "{icon} {name} events ({dates}):"},
    "none": {"he": "אין כרגע מה להראות כאן 🤷", "en": "Nothing to show here right now 🤷"},
}
# Whole messages (after understand.clean: casefolded, apostrophes kept) that ask for the week's top.
TOP_WORDS = {
    "הכי חם השבוע", "מה חם השבוע", "הכי חם", "מה חם", "הכי מבוקש השבוע", "הכי מבוקשים השבוע", "טופ השבוע",
    "top this week", "top of the week", "hot this week", "what's hot this week", "whats hot this week", "what's hot",
    "whats hot", "trending",
}  # fmt: skip


def _t(key: str, lang: str, **values: object) -> str:
    return TEXT[key][lang].format(**values)


def _icon(h: Holiday) -> str:
    return "🕎" if h.key == "hanukkah" else "🎊"


def _coming(cfg: Settings) -> list[Holiday]:
    today = understand.today_in(cfg)
    return [h for h in upcoming(cfg, today) if h.start <= today + timedelta(days=cfg.holiday_pages.rail_days)]


def rows(cfg: Settings, lang: str) -> list[list[Button]]:
    """The menu's row: the week's top and each coming holiday (none: no row)."""
    row = [Button(_t("top_button", lang), PREFIX + FIRST_TOP)] if cfg.weekly_top.enabled else []
    row += [
        Button(_t("holiday_button", lang, icon=_icon(h), name=h.name(lang)), f"{PREFIX}h:{h.key}:0")
        for h in _coming(cfg)
    ]
    return [row] if row else []


def wants_top(text: str, cfg: Settings) -> bool:
    return understand.clean(text, cfg.bot.max_text_length) in TOP_WORDS


async def press(conv: Conversation, chat_id: str, lang: str, action: str) -> Reply:
    """``action``: ``t:<offset>`` or ``h:<key>:<offset>`` (without the prefix)."""
    cfg = conv.settings()
    *head, offset = action.split(":")
    kind, key, *_ = [*head, "", ""]
    start, n = int(offset) if offset.isascii() and offset.isdigit() else 0, cfg.bot.results
    holiday = next((h for h in _coming(cfg) if h.key == key), None) if kind == "h" else None

    def work(session: Session) -> tuple[list[EventRow], bool, dict[str, dict[str, Any]]]:
        if kind == "h":
            found = seo._upcoming(session, *where(holiday), limit=start + n + 1) if holiday else []
        else:
            found = top(session, cfg, datetime.now(ZoneInfo(cfg.timezone)))
        page = found[start : start + n]
        usage.record(
            session,
            conv.channel,
            chat_id,
            "holiday" if kind == "h" else "top",
            q=holiday.key if holiday else None,
            results=len(page),
        )
        return page, len(found) > start + n, refs.extras(session, page)

    page, more, extras = await conv._in_db(work)
    if not page:
        return Reply(_t("none", lang), buttons=conv._starters(lang))
    if start:
        intro = "👇"
    elif holiday:
        intro = _t("holiday", lang, icon=_icon(holiday), name=holiday.name(lang), dates=_dates(holiday))
    else:
        intro = _t("top", lang)
    row = [Button(label("more", lang), PREFIX + ":".join([*head, str(start + n)]))] if more else []
    row.append(Button(label("menu_home", lang), MENU + "h"))
    return Reply(intro, await cards(page, extras, cfg, lang), [row])
