"""Telegram updates -> the conversation -> messages. Private chats only (a group gets nothing); inline
mode ("@bot techno tonight") works in any chat. A failure is logged and answered with a short apology."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Awaitable, Callable

from telegram import BotCommand, MenuButtonCommands, Update
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    AIORateLimiter,
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    InlineQueryHandler,
    MessageHandler,
    filters,
)  # fmt: skip

from partylinks.bot.core.conversation import Conversation, lang_from_app
from partylinks.bot.core.flood import Flood
from partylinks.bot.core.phrases import say
from partylinks.bot.core.reply import Reply
from partylinks.bot.core.understand import lang_of
from partylinks.bot.telegram import render
from partylinks.config import Settings
from partylinks.db import Database

log = logging.getLogger(__name__)
CHANNEL = "telegram"
PRIVATE = filters.ChatType.PRIVATE
INLINE_CACHE_SECONDS = 60
CONCURRENT_UPDATES = 16
COMMANDS = {
    "he": [("start", "התחלה"), ("alerts", "ההתראות שלי"), ("reminders", "התזכורות והמעקבים שלי"), ("digest", "סיכום שבועי לסופ״ש"),
           ("lang", "English / עברית"), ("help", "עזרה"), ("stop", "מחיקת כל המידע שלי")],
    "en": [("start", "Start"), ("alerts", "My alerts"), ("reminders", "My reminders and watches"), ("digest", "Weekly weekend picks"),
           ("lang", "עברית / English"), ("help", "Help"), ("stop", "Delete all my data")],
}  # fmt: skip
# What an empty chat shows before /start (≤512 characters), and the profile's "about" (≤120).
DESCRIPTIONS = {
    "he": (
        "🎉 SPOTX: כל המסיבות, ההופעות והאירועים בישראל ובעולם, במקום אחד.\n\n"
        "כתבו לי כמו לחבר: \"טכנו בתל אביב הערב\", \"משהו לילדים בשבת בחיפה\", \"הופעה בברלין בדצמבר\".\n\n"
        "🎟 קישור לכרטיסים ולאירוע · ⏰ תזכורת לפני · 👁 מעקב מחיר · 🔔 התראות על אירועים חדשים · 📅 ליומן\n\n"
        "חינם, בלי הרשמה. לחצו התחל ↓"
    ),
    "en": (
        "🎉 SPOTX: every party, gig and event in Israel and abroad, in one place.\n\n"
        "Write to me like to a friend: \"techno in Tel Aviv tonight\", \"something for kids on Saturday in Haifa\", "
        "\"a concert in Berlin in December\".\n\n"
        "🎟 Ticket and event links · ⏰ Reminders · 👁 Price watch · 🔔 Alerts on new events · 📅 To your calendar\n\n"
        "Free, no sign-up. Tap Start ↓"
    ),
}
ABOUT = {
    "he": "מסיבות, הופעות ואירועים בישראל ובעולם: חיפוש, כרטיסים, תזכורות והתראות. בחינם. spotx.fun",
    "en": "Parties, gigs and events in Israel and abroad: search, tickets, reminders and alerts. Free. spotx.fun",
}


def chat_tag(chat_id: int | str) -> str:
    """What logs carry instead of the chat's id."""
    return hashlib.sha256(str(chat_id).encode()).hexdigest()[:10]


class Handlers:
    def __init__(self, settings: Callable[[], Settings], db: Database) -> None:
        self.settings = settings
        self.talk = Conversation(settings, db, CHANNEL)
        self.flood = Flood()

    def install(self, app: Application) -> None:
        app.add_handler(CommandHandler("start", self.on_start, filters=PRIVATE))
        app.add_handler(CommandHandler("help", self.on_help, filters=PRIVATE))
        app.add_handler(CommandHandler("alerts", self.on_alerts, filters=PRIVATE))
        app.add_handler(CommandHandler("lang", self.on_lang, filters=PRIVATE))
        app.add_handler(CommandHandler("reminders", self.on_reminders, filters=PRIVATE))
        app.add_handler(CommandHandler("digest", self.on_digest, filters=PRIVATE))
        app.add_handler(CommandHandler("stop", self.on_stop, filters=PRIVATE))
        app.add_handler(MessageHandler(PRIVATE & filters.LOCATION, self.on_location))
        app.add_handler(MessageHandler(PRIVATE & filters.TEXT & ~filters.COMMAND, self.on_text))
        app.add_handler(MessageHandler(PRIVATE & ~filters.TEXT & ~filters.LOCATION & ~filters.COMMAND, self.on_other))
        app.add_handler(CallbackQueryHandler(self.on_press))
        app.add_handler(InlineQueryHandler(self.on_inline))
        app.add_error_handler(self.on_error)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _who(update: Update) -> tuple[str, str]:
        user, chat = update.effective_user, update.effective_chat
        return str(chat.id if chat else user.id if user else ""), lang_from_app(user.language_code if user else None)

    async def _answer(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, make: Callable[[str, str], Awaitable[Reply]]
    ) -> None:
        chat_id, lang = self._who(update)
        if not chat_id:
            return
        verdict = self.flood.check(chat_id, self.settings().bot.messages_per_minute)
        if verdict != "ok":
            if verdict == "warn":
                await context.bot.send_message(chat_id, say("slow_down", lang))
            return
        await context.bot.send_chat_action(chat_id, "typing")
        reply = await make(chat_id, lang)
        await render.send(context.bot, chat_id, reply, reply_lang(reply, lang))

    # ------------------------------------------------------------------ updates
    async def on_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._answer(update, context, self.talk.start)

    async def on_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._answer(update, context, lambda c, l: self.talk.text(c, "עזרה" if l == "he" else "help", l))

    async def on_alerts(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._answer(update, context, self.talk.alerts)

    async def on_reminders(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._answer(update, context, self.talk.reminders)

    async def on_digest(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._answer(update, context, self.talk.digest)

    async def on_lang(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._answer(update, context, lambda c, _: self.talk.set_lang(c))

    async def on_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._answer(update, context, self.talk.stop)

    async def on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        text = update.effective_message.text if update.effective_message else ""
        await self._answer(update, context, lambda c, l: self.talk.text(c, text or "", l))

    async def on_location(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        place = update.effective_message.location if update.effective_message else None
        if place is None:
            return
        await self._answer(update, context, lambda c, l: self.talk.location(c, place.latitude, place.longitude, l))

    async def on_other(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """A sticker, a voice message, a photo: the bot reads text and locations only."""
        chat_id, lang = self._who(update)
        if chat_id:
            await context.bot.send_message(chat_id, say("only_text", lang))

    async def on_press(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None:
            return
        try:
            await query.answer()  # stops the button's spinner at once
        except BadRequest:  # a press too old to answer: still handled below
            pass
        if query.message is None or not query.data or query.message.chat.type != "private":
            return
        await self._answer(update, context, lambda c, l: self.talk.press(c, query.data or "", l))

    async def on_inline(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.inline_query
        if query is None or len(query.query.strip()) < 2 or query.from_user is None:
            return
        # Asked on every keystroke: its own budget, a few times a chat's.
        if self.flood.check(f"inline:{query.from_user.id}", self.settings().bot.inline_per_minute) != "ok":
            return
        lang = lang_from_app(query.from_user.language_code if query.from_user else None)
        found = await self.talk.inline(query.query, lang)
        await query.answer(render.inline_results(found), cache_time=INLINE_CACHE_SECONDS, is_personal=False)

    async def on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        tag = chat_tag(update.effective_chat.id) if isinstance(update, Update) and update.effective_chat else "-"
        log.error("bot: update of chat %s failed", tag, exc_info=context.error)
        if isinstance(update, Update) and update.effective_chat and update.effective_chat.type == "private":
            _, lang = self._who(update)
            try:
                await context.bot.send_message(update.effective_chat.id, say("error", lang))
            except TelegramError:  # the apology itself failed (blocked, network): logged above already
                log.info("bot: apology to chat %s not sent", tag)


def reply_lang(reply: Reply, fallback: str) -> str:
    """The language of the reply's keyboard: the one its text is in."""
    return lang_of(reply.text, fallback)


def build(token: str, settings: Callable[[], Settings], db: Database) -> Application:
    """The bot, fed by our webhook (no polling): ``Application.update_queue`` takes what Telegram posts."""
    app = (
        Application.builder().token(token).updater(None)
        .rate_limiter(AIORateLimiter(max_retries=2))
        .concurrent_updates(CONCURRENT_UPDATES)  # one chat's slow search never holds up the others
        .build()
    )  # fmt: skip
    Handlers(settings, db).install(app)
    return app


async def describe(app: Application) -> None:
    """The command menu, the description and the about text, in Hebrew and in English."""
    for lang, commands in COMMANDS.items():
        code = None if lang == "he" else "en"
        await app.bot.set_my_commands([BotCommand(c, d) for c, d in commands], language_code=code)
        await app.bot.set_my_description(DESCRIPTIONS[lang], language_code=code)
        await app.bot.set_my_short_description(ABOUT[lang], language_code=code)
    await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
