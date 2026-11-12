"""The conversation: a message, a shared location or a pressed button -> a Reply. Knows nothing about
the chat app: a chat is (channel, chat id). Database work runs in a thread (the event loop never waits
on it); forecasts are fetched on the loop."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from partylinks.bot.core import consent, digest, event_actions, menu, navigate, understand, usage
from partylinks.bot.core.card import cards
from partylinks.bot.core.find import Found, search, search_or_widen
from partylinks.bot.core.phrases import label, say
from partylinks.bot.core.reply import (
    ALERT,
    ALERT_NO,
    ALERT_YES,
    CONSENT,
    DELETE_ALERT,
    DIGEST,
    LANG,
    MAX_ACTION_BYTES,
    MENU,
    MORE,
    REFINE,
    SEARCH,
    Button,
    Card,
    Reply,
)  # fmt: skip
from partylinks.bot.models import BotAlertRow, BotChatRow
from partylinks.config import Settings
from partylinks.db import Database, utcnow

log = logging.getLogger(__name__)
LANGS = ("he", "en")
ALERT_QUERY_MAX = event_actions.ALERT_QUERY_MAX
# Refine buttons: the words a follow-up adds ("and tonight"), in each language.
REFINE_WORDS = {"tonight": {"he": "הערב", "en": "tonight"}, "weekend": {"he": "בסופ״ש", "en": "this weekend"}}


DEFAULT_LANG = "he"  # like the site: Hebrew first, English when the user writes English or picks /lang


def _fits(action: str) -> bool:
    return len(action.encode()) <= MAX_ACTION_BYTES


class Conversation:
    """One per channel. ``settings``: a callable, so the admin's changes apply without a restart."""

    def __init__(self, settings: Callable[[], Settings], db: Database, channel: str) -> None:
        self.settings, self.db, self.channel = settings, db, channel

    # ------------------------------------------------------------------ the chat's row
    def _chat(self, session: Session, chat_id: str, lang_hint: str) -> BotChatRow:
        mine = select(BotChatRow).where(BotChatRow.channel == self.channel, BotChatRow.chat_id == chat_id)
        chat = session.scalar(mine)
        if chat is None:  # two first messages at once: one row, the other insert does nothing
            session.execute(
                insert(BotChatRow)
                .values(channel=self.channel, chat_id=chat_id, lang=lang_hint, lang_chosen=False, last_offset=0, last_cheap=False)
                .on_conflict_do_nothing()
            )  # fmt: skip
            chat = session.scalars(mine).one()
        chat.blocked_at = None  # writing again after blocking the bot: it may send again
        chat.seen_at = utcnow()  # silent for bot.inactive_days: deleted (consent.prune)
        return chat

    def _recent(self, chat: BotChatRow) -> str | None:
        """The chat's last search, when recent enough to continue ("ומחר?")."""
        window = timedelta(minutes=self.settings().bot.memory_minutes)
        return chat.last_query if chat.last_query is not None and chat.last_at and chat.last_at > utcnow() - window else None

    async def _in_db(self, work: Callable[[Session], Any]) -> Any:
        def run() -> Any:
            with self.db.session() as session:
                return work(session)

        return await asyncio.to_thread(run)

    # ------------------------------------------------------------------ entry points
    async def _use(self, chat_id: str | None, kind: str, q: str | None = None, results: int | None = None) -> None:
        """One row for the admin's bot tab (usage.py)."""
        await self._in_db(lambda s: usage.record(s, self.channel, chat_id, kind, q, results))

    async def gate(self, chat_id: str) -> Reply | None:
        """The entry screen, until the chat agreed (consent.py); None once it has."""
        agreed = await self._in_db(lambda s: consent.given(s, self.channel, chat_id))
        return None if agreed else consent.screen(self.settings())

    async def menu(self, chat_id: str, lang_hint: str) -> Reply:
        lang = await self._in_db(lambda s: self._chat(s, chat_id, lang_hint).lang)
        return menu.home(self.settings(), lang)

    async def start(self, chat_id: str, lang_hint: str) -> Reply:
        def work(session: Session) -> str:
            usage.record(session, self.channel, chat_id, "start")
            chat = self._chat(session, chat_id, lang_hint)
            if not chat.lang_chosen:  # a new start speaks Hebrew, until /lang says otherwise
                chat.lang = DEFAULT_LANG
            return chat.lang

        lang = await self._in_db(work)
        home = menu.home(self.settings(), lang)
        return Reply(say("welcome", lang), buttons=home.buttons, photo=consent.banner(self.settings(), lang), after=say("keyboard_hint", lang), ask_location=True)

    async def text(self, chat_id: str, text: str, lang_hint: str) -> Reply:
        cfg = self.settings()

        def work(session: Session) -> tuple[str, understand.Message, BotChatRow]:
            chat = self._chat(session, chat_id, lang_hint)
            if not chat.lang_chosen:
                chat.lang = understand.lang_of(text, chat.lang)
            usage.record(session, self.channel, chat_id, "message")
            return chat.lang, understand.read(text, cfg, self._recent(chat)), chat

        lang, message, chat = await self._in_db(work)
        if text.strip() in (label("my_events", "he"), label("my_events", "en")):  # the keyboard's button
            return await self.reminders(chat_id, lang)
        if text.strip() in (label("menu_home", "he"), label("menu_home", "en")):
            return menu.home(cfg, lang)
        if message.kind in ("greeting", "empty"):
            return Reply(say("greeting", lang), buttons=self._starters(lang))
        if message.kind == "thanks":
            return Reply(say("thanks", lang))
        if message.kind == "help":
            return Reply(say("help", lang), buttons=self._starters(lang))
        if message.kind == "more":
            return await self._more(chat_id, lang_hint)
        if message.kind == "alerts":
            return await self.alerts(chat_id, lang_hint)
        if message.kind == "near" or (message.near and chat.last_lat is None):
            await self._remember(chat_id, message.query, message.cheap, None, 0)  # used when the location comes
            return Reply(say("ask_location", lang), ask_location=True)
        located = chat.last_lat is not None and chat.last_lng is not None
        # A follow-up of a "near me" search ("ומחר?", "הכי זול") stays near, unless it names a place of its own.
        names_a_place = bool(understand.kinds(understand.clean(text, cfg.bot.max_text_length), cfg, understand.today_in(cfg))["place"])
        keep = message.near or (message.follow_up and not names_a_place)
        near = (chat.last_lat, chat.last_lng) if located and keep else None
        return await self._search(chat_id, lang, message.query, message.cheap, near)

    async def location(self, chat_id: str, lat: float, lng: float, lang_hint: str) -> Reply:
        """A shared location: the recent search (or everything) near it."""
        def work(session: Session) -> BotChatRow:
            usage.record(session, self.channel, chat_id, "location")
            return self._chat(session, chat_id, lang_hint)

        chat = await self._in_db(work)
        return await self._search(chat_id, chat.lang, self._recent(chat) or "", chat.last_cheap, (lat, lng))

    async def press(self, chat_id: str, action: str, lang_hint: str) -> Reply:
        if action.startswith(CONSENT):  # before anything is kept: no row, no usage tag until "I agree"
            if action == consent.AGREE:
                await consent.agree(self, chat_id, lang_hint)
                return await self.start(chat_id, lang_hint)
            await consent.decline(self, chat_id)
            return Reply(say("declined", lang_hint))

        def work(session: Session) -> BotChatRow:
            usage.record(session, self.channel, chat_id, "press", q=action.partition(":")[0] or action)
            return self._chat(session, chat_id, lang_hint)

        chat = await self._in_db(work)
        lang = chat.lang
        if action == MORE:
            return await self._more(chat_id, lang_hint)
        if action == ALERT:
            return await self._alert_offer(chat_id, lang)
        if action == ALERT_YES:
            return await self._alert_save(chat_id, lang)
        if action == ALERT_NO:
            await self._in_db(lambda s: setattr(self._chat(s, chat_id, lang), "pending_alert", None))
            return Reply(say("alert_cancelled", lang))
        if action.startswith(DELETE_ALERT):
            return await self._alert_delete(chat_id, lang, action.removeprefix(DELETE_ALERT))
        if action.startswith(LANG):
            return await self.set_lang(chat_id, action.removeprefix(LANG))
        if action.startswith(SEARCH):
            return await self._search(chat_id, lang, action.removeprefix(SEARCH), False, None)
        if action.startswith(REFINE):
            return await self._refine(chat, lang, action.removeprefix(REFINE))
        if action.startswith(MENU):
            return await menu.press(self, chat_id, lang, action)
        if action.startswith(DIGEST):
            return await digest.choose(self, chat_id, lang, action.removeprefix(DIGEST))
        if action.startswith(navigate.NAVIGATE):
            return await navigate.press(self, chat_id, lang, action.removeprefix(navigate.NAVIGATE))
        if (reply := await event_actions.press(self, chat_id, lang, action)) is not None:
            return reply
        log.info("bot: unknown button %r", action[:40])
        return Reply(say("greeting", lang), buttons=self._starters(lang))

    async def inline(self, text: str, lang_hint: str) -> list[Card]:
        """Cards for "@bot ..." typed in any chat: no memory, no alerts, no weather (Telegram asks on every
        keystroke and waits a few seconds at most)."""
        cfg = self.settings()
        message = understand.read(text, cfg)
        lang = understand.lang_of(text, lang_hint)
        if message.kind != "search":
            return []
        def work(session: Session) -> Found:
            usage.record(session, self.channel, None, "inline", q=message.query)
            return search(session, cfg, message.query, limit=cfg.bot.inline_results, cheap=message.cheap)

        found: Found = await self._in_db(work)
        return await cards(found.rows, found.extras, cfg, lang, with_weather=False)

    async def set_lang(self, chat_id: str, lang: str | None = None) -> Reply:
        """``lang`` he / en, or None: the other one."""

        def work(session: Session) -> str:
            chat = self._chat(session, chat_id, lang or "he")
            chat.lang = lang if lang in LANGS else ("en" if chat.lang == "he" else "he")
            chat.lang_chosen = True
            return chat.lang

        chosen = await self._in_db(work)
        return Reply(say("lang_set", chosen), buttons=self._starters(chosen))

    async def stop(self, chat_id: str, lang_hint: str) -> Reply:
        """Everything about the chat is deleted (alerts with it); writing again starts over."""

        def work(session: Session) -> str:
            chat = self._chat(session, chat_id, lang_hint)
            lang = chat.lang
            session.delete(chat)
            usage.forget(session, chat_id)
            usage.record(session, self.channel, None, "stop")
            return lang

        return Reply(say("stopped", await self._in_db(work)))

    async def alerts(self, chat_id: str, lang_hint: str) -> Reply:
        def work(session: Session) -> tuple[str, list[tuple[int, str]]]:
            chat = self._chat(session, chat_id, lang_hint)
            rows = session.execute(
                select(BotAlertRow.id, BotAlertRow.query)
                .where(BotAlertRow.chat_ref == chat.id, BotAlertRow.active.is_(True)).order_by(BotAlertRow.id)
            ).all()  # fmt: skip
            return chat.lang, [(i, q) for i, q in rows]

        lang, rows = await self._in_db(work)
        if not rows:
            return Reply(say("alerts_empty", lang))
        return Reply(say("alerts_list", lang), buttons=[[Button(f"🗑 {q}", f"{DELETE_ALERT}{i}")] for i, q in rows])

    async def reminders(self, chat_id: str, lang_hint: str) -> Reply:
        return await event_actions.mine(self, chat_id, lang_hint)

    async def digest(self, chat_id: str, lang_hint: str) -> Reply:
        return await digest.ask(self, chat_id, lang_hint)

    # ------------------------------------------------------------------ search
    async def _remember(self, chat_id: str, query: str, cheap: bool, near: tuple[float, float] | None, offset: int) -> None:
        def work(session: Session) -> None:
            chat = self._chat(session, chat_id, "he")
            chat.last_query, chat.last_cheap, chat.last_offset, chat.last_at = query, cheap, offset, utcnow()
            chat.last_lat, chat.last_lng = near if near else (None, None)

        await self._in_db(work)

    async def _search(self, chat_id: str, lang: str, query: str, cheap: bool, near: tuple[float, float] | None) -> Reply:
        cfg = self.settings()
        today = understand.today_in(cfg)
        def work(session: Session) -> Found:
            found = search_or_widen(session, cfg, query, today, limit=cfg.bot.results, cheap=cheap, near=near)
            usage.record(session, self.channel, chat_id, "search", q=query or ("📍" if near else None), results=found.total)
            return found

        found: Found = await self._in_db(work)
        km = f"{cfg.bot.near_km:g}"
        if not found.rows:
            if near:
                return Reply(say("near_none", lang, km=km), buttons=self._starters(lang))
            # Nothing yet is the best time for an alert: "tell me when they announce a show".
            await self._remember(chat_id, query, cheap, None, 0)
            text = say("none", lang, q=query) + ("\n" + say("alert_hint", lang) if query else "")
            reply = Reply(text, buttons=[[Button(label("alert", lang), ALERT)]] if query else [])
            if found.suggestion and _fits(SEARCH + found.suggestion):
                reply.buttons.insert(0, [Button(f"🔎 {say('did_you_mean', lang, q=found.suggestion)}", SEARCH + found.suggestion)])
            reply.buttons += self._starters(lang)
            return reply
        await self._remember(chat_id, found.query, cheap, near, len(found.rows))
        if near:
            intro = say("found_near", lang, n=found.total, km=km)
        elif found.widened:
            intro = say(f"widened_{found.widened}", lang, n=found.total)
        elif found.total == 1:
            intro = say("found_one", lang)
        else:
            intro = say("found_cheap" if cheap else "found", lang, n=found.total)
        return await self._page(found, lang, intro, near, more=found.total > len(found.rows))

    async def _page(self, found: Found, lang: str, intro: str, near: tuple[float, float] | None, more: bool) -> Reply:
        cfg = self.settings()
        first = [Button(label("more", lang), MORE)] if more else []
        if found.query and not near:
            first.append(Button(label("alert", lang), ALERT))
        refine = [Button(label(k, lang), REFINE + k) for k in ("tonight", "weekend", "cheap")]
        rows = [r for r in (first, refine) if r]
        return Reply(intro, await cards(found.rows, found.extras, cfg, lang, near), rows, after=say("next", lang))

    async def _more(self, chat_id: str, lang_hint: str) -> Reply:
        cfg = self.settings()

        def work(session: Session) -> tuple[BotChatRow, Found | None]:
            chat = self._chat(session, chat_id, lang_hint)
            query = self._recent(chat)
            if query is None:
                return chat, None
            near = (chat.last_lat, chat.last_lng) if chat.last_lat is not None and chat.last_lng is not None else None
            found = search(session, cfg, query, offset=chat.last_offset, limit=cfg.bot.results, cheap=chat.last_cheap, near=near)
            chat.last_offset += len(found.rows)
            chat.last_at = utcnow()
            return chat, found

        chat, found = await self._in_db(work)
        if found is None:
            return Reply(say("no_search", chat.lang), buttons=self._starters(chat.lang))
        if not found.rows:
            return Reply(say("no_more", chat.lang), buttons=self._starters(chat.lang))
        near = (chat.last_lat, chat.last_lng) if chat.last_lat is not None and chat.last_lng is not None else None
        return await self._page(found, chat.lang, "👇", near, more=found.total > chat.last_offset)

    async def _refine(self, chat: BotChatRow, lang: str, key: str) -> Reply:
        previous = self._recent(chat)
        near = (chat.last_lat, chat.last_lng) if chat.last_lat is not None and chat.last_lng is not None else None
        if key == "cheap":
            return await self._search(chat.chat_id, lang, previous or "", True, near)
        words = REFINE_WORDS.get(key, {}).get(lang)
        if words is None:
            return Reply(say("greeting", lang), buttons=self._starters(lang))
        if previous is None:
            return await self._search(chat.chat_id, lang, words, False, None)
        message = understand.read(("ו" if lang == "he" else "and ") + words, self.settings(), previous)
        return await self._search(chat.chat_id, lang, message.query, chat.last_cheap, near)

    def _starters(self, lang: str) -> list[list[Button]]:
        """Ideas for a chat with no search yet: tonight, this weekend, free. One a row: three in a row are cut
        to "…" under a short message."""
        return [
            [Button(label("tonight", lang), SEARCH + REFINE_WORDS["tonight"][lang])],
            [Button(label("weekend", lang), SEARCH + REFINE_WORDS["weekend"][lang])],
            [Button(label("free", lang), SEARCH + ("חינם" if lang == "he" else "free"))],
        ]

    # ------------------------------------------------------------------ alerts
    async def _alert_offer(self, chat_id: str, lang: str) -> Reply:
        def work(session: Session) -> str | None:
            chat = self._chat(session, chat_id, lang)
            chat.pending_alert = self._recent(chat) or None
            return chat.pending_alert

        query = await self._in_db(work)
        if not query:
            return Reply(say("alert_none_pending", lang))
        if len(query) > ALERT_QUERY_MAX:
            return Reply(say("alert_too_long", lang, n=ALERT_QUERY_MAX))
        return Reply(say("alert_ask", lang, q=query), buttons=[[Button(label("yes", lang), ALERT_YES), Button(label("no", lang), ALERT_NO)]])

    async def _alert_save(self, chat_id: str, lang: str) -> Reply:
        limit = self.settings().bot.max_alerts

        def work(session: Session) -> tuple[str, str | None]:
            chat = self._chat(session, chat_id, lang)
            # Locked and read again: a second tap on "yes" waits here, then finds nothing pending.
            session.scalar(
                select(BotChatRow).where(BotChatRow.id == chat.id).with_for_update().execution_options(populate_existing=True)
            )  # fmt: skip
            query, chat.pending_alert = chat.pending_alert, None
            if not query:
                return "alert_none_pending", None
            mine = select(BotAlertRow).where(BotAlertRow.chat_ref == chat.id, BotAlertRow.query == query)
            if (existing := session.scalar(mine)) is not None:
                if existing.active:
                    return "alert_exists", query
                existing.active = True
                return "alert_saved", query
            count = session.scalar(
                select(func.count()).select_from(BotAlertRow).where(BotAlertRow.chat_ref == chat.id, BotAlertRow.active.is_(True))
            )  # fmt: skip
            if (count or 0) >= limit:
                return "alert_full", None
            session.add(BotAlertRow(chat_ref=chat.id, query=query))
            return "alert_saved", query

        key, query = await self._in_db(work)
        return Reply(say(key, lang, q=query or "", n=limit))

    async def _alert_delete(self, chat_id: str, lang: str, alert_id: str) -> Reply:
        if not (alert_id.isascii() and alert_id.isdigit()):
            return Reply(say("alerts_empty", lang))

        def work(session: Session) -> str | None:
            chat = self._chat(session, chat_id, lang)
            mine = (BotAlertRow.id == int(alert_id), BotAlertRow.chat_ref == chat.id)
            query = session.scalar(select(BotAlertRow.query).where(*mine))
            session.execute(delete(BotAlertRow).where(*mine))
            return query

        query = await self._in_db(work)
        listing = await self.alerts(chat_id, lang)
        if query is None:
            return listing
        listing.text = say("alert_deleted", lang, q=query) + "\n\n" + listing.text
        return listing
