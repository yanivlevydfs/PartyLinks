"""The entry screen: what the bot is, that it is free, the disclaimer and what it keeps, in Hebrew and in
English, with "I agree" / "Decline". Nothing about a chat is kept before "I agree" (no row, no usage
tag); "Decline" deletes whatever there was. A chat silent for ``bot.inactive_days`` is deleted."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from partylinks.bot.core import usage
from partylinks.bot.core.reply import CONSENT, Button, Reply
from partylinks.bot.models import BotChatRow
from partylinks.config import Settings
from partylinks.db import Database, utcnow

if TYPE_CHECKING:
    from partylinks.bot.core.conversation import Conversation

AGREE, DECLINE = CONSENT + "yes", CONSENT + "no"
BANNER = "/bot-banner-{lang}.png?v=2"  # web/public, made by scripts/bot_banner.py; ?v: Telegram caches by URL, bump it with the picture


def banner(cfg: Settings, lang: str = "he") -> str:
    """The banner in ``lang`` (the entry screen, in both languages, shows the Hebrew one)."""
    return (cfg.bot.banner_url or cfg.site.base_url.rstrip("/") + BANNER).format(lang=lang)


def text(cfg: Settings) -> str:
    """HTML, at most a caption's 1024 characters (tests/test_bot_menu.py)."""
    site = cfg.site.base_url.rstrip("/")
    months = round(cfg.bot.inactive_days / 30)
    return (
        "<b>🎉 ברוכים הבאים ל-SPOTX</b>\n"
        "כל המסיבות, ההופעות והאירועים בישראל ובעולם: חיפוש לפי קטגוריה, אזור ותאריך, קישור לכרטיסים, "
        "תזכורות והתראות. <b>בחינם.</b>\n"
        "⚠️ המידע מוצג כמו שהוא מאתרי הכרטיסים; איננו המפיקים ואיננו אחראים לשינויים או לטעויות.\n"
        f"🔒 לאחר האישור נשמור את מזהה הטלגרם שלכם, החיפוש האחרון וההתראות שתבחרו, כדי לשלוח לכם אותן. "
        f"משתמש שאינו פעיל {months} חודשים נמחק. /stop מוחק הכל בכל עת. "
        f'<a href="{site}/privacy">מדיניות הפרטיות</a>\n\n'
        "<b>🎉 Welcome to SPOTX</b>\n"
        "Every party, gig and event in Israel and abroad: search by category, area and date, ticket links, "
        "reminders and alerts. <b>Free.</b>\n"
        "⚠️ Listings are shown as the ticket sites publish them; we are not the organizers and take no "
        "responsibility for changes or mistakes.\n"
        f"🔒 Once you agree we keep your Telegram ID, your last search and the alerts you choose, to send them. "
        f"Inactive for {months} months: deleted. /stop deletes everything. "
        f'<a href="{site}/privacy?lang=en">Privacy policy</a>'
    )


def screen(cfg: Settings) -> Reply:
    buttons = [[Button("✅ אישור והמשך / I agree", AGREE)], [Button("❌ ביטול / Decline", DECLINE)]]
    return Reply(text(cfg), buttons=buttons, photo=banner(cfg), html=True)


def given(session: Session, channel: str, chat_id: str) -> bool:
    found = session.scalar(
        select(BotChatRow.consent_at).where(BotChatRow.channel == channel, BotChatRow.chat_id == chat_id)
    )  # fmt: skip
    return found is not None


async def agree(conv: Conversation, chat_id: str, lang: str) -> None:
    def work(session: Session) -> None:
        chat = conv._chat(session, chat_id, lang)
        chat.consent_at = chat.consent_at or utcnow()
        usage.record(session, conv.channel, chat_id, "consent")

    await conv._in_db(work)


async def decline(conv: Conversation, chat_id: str) -> None:
    """Whatever there was about the chat goes (a returning user who declines: as /stop)."""

    def work(session: Session) -> None:
        session.execute(delete(BotChatRow).where(BotChatRow.channel == conv.channel, BotChatRow.chat_id == chat_id))
        usage.forget(session, chat_id)
        usage.record(session, conv.channel, None, "decline")

    await conv._in_db(work)


def prune(settings: Settings, db: Database) -> int:
    """Chats silent for ``bot.inactive_days`` are deleted (their alerts, reminders and watches cascade)."""
    cutoff = utcnow() - timedelta(days=settings.bot.inactive_days)
    with db.session() as session:
        last = func.coalesce(BotChatRow.seen_at, BotChatRow.created_at)
        result: Any = session.execute(delete(BotChatRow).where(last < cutoff))
        return result.rowcount or 0
