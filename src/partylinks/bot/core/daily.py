"""☀️ Daily tip: every day at ``bot.daily_hour`` up to ``bot.daily_results`` of tonight's picks (core/picks.py)
in the chat's area and taste. Opt-in only (/daily or the menu's button, then an area; the taste is the chat's
last search without its place and date, else everything); a second /daily or press stops it. Kept on the
chat's row (``daily_query``, None when off; ``daily_sent_on``), so /stop and "Decline" delete it with the row.
An event is on one night only (its ``local_date``), so one tip a day never repeats an event."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from partylinks.bot.core import menu, understand, usage
from partylinks.bot.core.digest import Digest
from partylinks.bot.core.phrases import label
from partylinks.bot.core.picks import picks
from partylinks.bot.core.reply import Button, Reply
from partylinks.bot.models import BotChatRow
from partylinks.config import Settings
from partylinks.db import Database

if TYPE_CHECKING:
    from partylinks.bot.core.conversation import Conversation

PREFIX = "dy:"
EVERYWHERE = "*"
TEXT: dict[str, dict[str, str]] = {
    "button": {"he": "☀️ טיפ יומי", "en": "☀️ Daily tip"},
    "where": {
        "he": "☀️ כל יום ב-{h}:00 אשלח לכם את הכי שווים של הערב{q}. באיזה אזור?",
        "en": "☀️ Every day at {h}:00 I'll send you tonight's best{q}. Which area?",
    },
    "taste": {"he": " (כמו ״{q}״)", "en": ' (like "{q}")'},
    "on": {
        "he": "☀️ נרשמתם לטיפ היומי ({q}). ביטול: /daily",
        "en": "☀️ You're in for the daily tip ({q}). To stop: /daily",
    },
    "off": {"he": "ביטלתי את הטיפ היומי 👌", "en": "Daily tip stopped 👌"},
    "new": {"he": "☀️ הטיפ היומי: הכי שווים הערב ({q})", "en": "☀️ Your daily tip: tonight's best ({q})"},
}


def _t(key: str, lang: str, **values: object) -> str:
    return TEXT[key][lang].format(**values)


def button(lang: str) -> Button:
    """The menu's button."""
    return Button(_t("button", lang), PREFIX)


def _taste(conv: Conversation, chat: BotChatRow) -> str:
    cfg = conv.settings()
    last = conv._recent(chat) or ""
    parts = understand.kinds(last, cfg, understand.today_in(cfg))
    return understand.without(last, parts["date"] + parts["place"])


def _where(lang: str, hour: int, taste: str) -> Reply:
    i = menu._i(lang)
    areas = [Button(menu.AREA_LABELS[a][i], PREFIX + a) for a in menu.AREAS]
    rows = [*menu._pairs(areas), [Button(label("menu_everywhere", lang), PREFIX + EVERYWHERE)]]
    text = _t("where", lang, h=hour, q=_t("taste", lang, q=taste) if taste else "")
    return Reply(text, buttons=[*rows, [Button(label("menu_home", lang), menu.HOME)]])


async def press(conv: Conversation, chat_id: str, lang_hint: str, data: str) -> Reply:
    """``data`` empty (/daily, the menu's button): off when on, else the area buttons; else the area."""
    cfg = conv.settings()

    def work(session: Session) -> Reply:
        chat = conv._chat(session, chat_id, lang_hint)
        lang = chat.lang
        if not data and chat.daily_query is not None:
            chat.daily_query = None
            usage.record(session, conv.channel, chat_id, "daily_off")
            return Reply(_t("off", lang))
        taste = _taste(conv, chat)
        if not data:
            return _where(lang, cfg.bot.daily_hour, taste)
        area = menu.AREAS[data][menu._i(lang)] if data in menu.AREAS else ""
        chat.daily_query = f"{taste} {area}".strip()
        usage.record(session, conv.channel, chat_id, "daily_on", q=chat.daily_query)
        return Reply(_t("on", lang, q=chat.daily_query or label("everything", lang)))

    return await conv._in_db(work)


def due(settings: Settings, db: Database) -> list[Digest]:
    """Tonight's picks of every subscribed chat not sent one today, marked sent here (at most once a day).
    Nothing tonight: nothing."""
    today = understand.today_in(settings)
    out: list[Digest] = []
    with db.session() as session:
        chats = session.scalars(
            select(BotChatRow).where(BotChatRow.daily_query.is_not(None), BotChatRow.blocked_at.is_(None),
                                     or_(BotChatRow.daily_sent_on.is_(None), BotChatRow.daily_sent_on < today))
        ).all()  # fmt: skip
        found: dict[str, list[str]] = {}
        for chat in chats:
            q = chat.daily_query or ""
            if q not in found:
                found[q] = [r.id for r in picks(session, settings, q, "tonight", today, settings.bot.daily_results)]
            chat.daily_sent_on = today
            if found[q]:
                out.append(Digest(chat.id, chat.channel, chat.chat_id, chat.lang, q, found[q]))
    return out


def intro(item: Digest) -> str:
    """The text above a tip's cards."""
    return _t("new", item.lang, q=item.query or label("everything", item.lang))
