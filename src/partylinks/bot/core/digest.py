"""📬 /digest: the weekly picks for the weekend, opt-in only (``digest_on``), for everything or for the
chat's last search. Sent by the worker on ``bot.digest_weekday`` at ``bot.digest_hour``."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from partylinks.bot.core import usage
from partylinks.bot.core.phrases import WEEKDAY_NAMES, label, say
from partylinks.bot.core.picks import picks
from partylinks.bot.core.reply import DIGEST, Button, Reply
from partylinks.bot.core.understand import today_in
from partylinks.bot.models import BotChatRow
from partylinks.config import Settings
from partylinks.db import Database, utcnow

if TYPE_CHECKING:
    from partylinks.bot.core.conversation import Conversation

DIGEST_QUERY_MAX = 100


async def ask(conv: Conversation, chat_id: str, lang_hint: str) -> Reply:
    cfg = conv.settings()

    def work(session: Session) -> tuple[str, str | None, bool]:
        chat = conv._chat(session, chat_id, lang_hint)
        return chat.lang, conv._recent(chat), chat.digest_on

    lang, last, on = await conv._in_db(work)
    rows = [[Button(label("digest_all", lang), f"{DIGEST}all")]]
    if last and len(last) <= DIGEST_QUERY_MAX:
        rows.append([Button(label("digest_last", lang, q=last[:40]), f"{DIGEST}last")])
    if on:
        rows.append([Button(label("digest_stop", lang), f"{DIGEST}off")])
    return Reply(say("digest_ask", lang, d=WEEKDAY_NAMES[lang][cfg.bot.digest_weekday]), buttons=rows)


async def choose(conv: Conversation, chat_id: str, lang_hint: str, choice: str) -> Reply:
    def work(session: Session) -> tuple[str, str]:
        chat = conv._chat(session, chat_id, lang_hint)
        if choice == "off":
            chat.digest_on, chat.digest_query = False, None
            usage.record(session, conv.channel, chat_id, "digest_off")
            return chat.lang, say("digest_off", chat.lang)
        query = conv._recent(chat) if choice == "last" else None
        chat.digest_on, chat.digest_query = True, query[:DIGEST_QUERY_MAX] if query else None
        usage.record(session, conv.channel, chat_id, "digest_on", q=chat.digest_query)
        return chat.lang, say("digest_on", chat.lang, q=chat.digest_query or label("everything", chat.lang))

    _, text = await conv._in_db(work)
    return Reply(text)


@dataclass
class Digest:
    chat_ref: int
    channel: str
    chat_id: str
    lang: str
    query: str | None
    event_ids: list[str]


def due(settings: Settings, db: Database) -> list[Digest]:
    """Every subscribed chat's picks, marked sent here (at most once a week). A chat with no picks gets
    nothing this week."""
    cfg, now = settings.bot, utcnow()
    today = today_in(settings)
    out: list[Digest] = []
    with db.session() as session:
        chats = session.scalars(
            select(BotChatRow).where(BotChatRow.digest_on.is_(True), BotChatRow.blocked_at.is_(None),
                                     or_(BotChatRow.digest_sent_at.is_(None), BotChatRow.digest_sent_at < now - timedelta(days=cfg.digest_again_days)))
        ).all()  # fmt: skip
        found: dict[str | None, list[str]] = {}
        for chat in chats:
            if chat.digest_query not in found:
                found[chat.digest_query] = [r.id for r in picks(session, settings, chat.digest_query, "weekend", today, cfg.digest_results)]
            chat.digest_sent_at = now
            if found[chat.digest_query]:
                out.append(Digest(chat.id, chat.channel, chat.chat_id, chat.lang, chat.digest_query, found[chat.digest_query]))
    return out
