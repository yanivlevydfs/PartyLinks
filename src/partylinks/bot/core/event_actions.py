"""The buttons under an event card and in its ➕ menu: ⏰ remind, 👁 watch, 🔁 similar, 📅 calendar,
👥 who's coming (a site plan), ⭐ follow the venue or an artist (an alert, after a confirm). Each carries
the event's short number (refs.py); a stale one answers "no longer available"."""

from __future__ import annotations

import secrets
from datetime import timedelta
from typing import TYPE_CHECKING
from urllib.parse import urlencode

from sqlalchemy.orm import Session

from partylinks import seo
from partylinks.bot.core import calendar, navigate, refs, remind, similar, usage
from partylinks.bot.core.card import cards
from partylinks.bot.core.phrases import duration, label, say
from partylinks.bot.core.reply import (
    ALERT_NO,
    ALERT_YES,
    FOLLOW,
    FORGET,
    ICS,
    MAX_ACTION_BYTES,
    OPTIONS,
    PLAN,
    REMIND,
    SIMILAR,
    WATCH,
    Button,
    Reply,
)  # fmt: skip
from partylinks.bot.models import BotChatRow
from partylinks.config import get_settings
from partylinks.db import EventRow
from partylinks.plans.models import PlanRow

if TYPE_CHECKING:
    from partylinks.bot.core.conversation import Conversation

PREFIXES = (REMIND, WATCH, SIMILAR, OPTIONS, ICS, PLAN, FOLLOW, FORGET)
ALERT_QUERY_MAX = 100  # alerts.criteria.Criteria.q (conversation uses it too)


def _short(title: str) -> str:
    cut = get_settings().bot.title_in_text
    return title if len(title) <= cut else title[: cut - 1] + "…"


def _and(parts: list[str], lang: str) -> str:
    return (" ו" if lang == "he" else " and ").join(parts)


def _follows(row: EventRow) -> list[str]:
    """What ⭐ can follow: the venue, then the lineup's first artists (each a search alert)."""
    names = [row.venue_name] if row.venue_name else []
    names += [a for a in (row.artists or [])[: get_settings().bot.follow_artists] if a and a not in names]
    return [n for n in names if len(n) <= ALERT_QUERY_MAX]


async def press(conv: Conversation, chat_id: str, lang: str, action: str) -> Reply | None:
    """A card button's answer; None when ``action`` is not one of them."""
    prefix = next((p for p in PREFIXES if action.startswith(p)), None)
    if prefix is None:
        return None
    if prefix == FORGET:
        return await _forget(conv, chat_id, lang, action.removeprefix(FORGET))
    ref, _, arg = action.removeprefix(prefix).partition(":")
    cfg = conv.settings()

    def work(session: Session) -> tuple[Reply | None, EventRow | None, str]:
        chat = conv._chat(session, chat_id, lang)
        row = refs.event(session, ref)
        if row is None:
            return Reply(say("gone", chat.lang)), None, chat.lang
        return act(session, chat, row, _short(row.title)), row, chat.lang

    def act(session: Session, chat: BotChatRow, row: EventRow, title: str) -> Reply | None:
        if prefix == REMIND:
            key, hours = remind.toggle_reminder(
                session, chat, row, cfg.bot.reminder_hours, cfg.bot.max_reminders, timedelta(minutes=cfg.bot.reminder_min_ahead_minutes)
            )
            usage.record(session, conv.channel, chat_id, "reminder_set" if key == "reminder_set" else key)
            h = _and([duration(x, chat.lang) for x in hours], chat.lang)
            return Reply(say(key, chat.lang, t=title, h=h, n=cfg.bot.max_reminders))
        if prefix == WATCH:
            key = remind.toggle_watch(session, chat, row, cfg.bot.max_watches)
            usage.record(session, conv.channel, chat_id, key)
            return Reply(say(key, chat.lang, t=title, n=cfg.bot.max_watches))
        if prefix == FOLLOW:
            return _follow(chat, row, arg)
        if prefix == PLAN:
            plan = PlanRow(id=secrets.token_urlsafe(9), event_id=row.id)  # the site's "who's coming?" (plans.routes)
            session.add(plan)
            usage.record(session, conv.channel, chat_id, "plan")
            url = f"{cfg.site.base_url.rstrip('/')}/plan/{plan.id}"
            share = "https://t.me/share/url?" + urlencode({"url": url, "text": say("plan_share", chat.lang, t=title)})
            return Reply(say("plan", chat.lang, u=url), buttons=[[Button(label("share", chat.lang), url=share)]])
        return None

    reply, row, lang = await conv._in_db(work)
    if reply is not None or row is None:
        return reply
    title = _short(row.title)
    page = f"{cfg.site.base_url.rstrip('/')}{seo.event_path(row)}"
    if prefix == SIMILAR:
        def find(session: Session) -> tuple[list[EventRow], dict]:
            rows = similar.find(session, cfg, row, cfg.bot.similar_results)
            usage.record(session, conv.channel, chat_id, "similar", results=len(rows))
            return rows, refs.extras(session, rows)

        rows, extras = await conv._in_db(find)
        if not rows:
            return Reply(say("similar_none", lang, t=title), buttons=conv._starters(lang))
        return Reply(say("similar", lang, t=title), await cards(rows, extras, cfg, lang))
    if prefix == ICS:
        await conv._in_db(lambda s: usage.record(s, conv.channel, chat_id, "calendar"))
        site = cfg.site.base_url.split("//")[-1].strip("/")
        return Reply(
            say("calendar", lang, t=title), buttons=[[Button(label("google_cal", lang), url=calendar.google(row, page))]],
            document=("event.ics", calendar.ics(row, page, site)),
        )  # fmt: skip
    # OPTIONS: the ➕ menu
    rows_ = [
        [Button(label("ics", lang), f"{ICS}{ref}"), Button(label("google_cal", lang), url=calendar.google(row, page))],
        [Button(label("plan", lang), f"{PLAN}{ref}")],
        [navigate.button(row, ref, lang)],
    ]
    for i, name in enumerate(_follows(row)):
        action_ = f"{FOLLOW}{ref}:{i}"
        if len(action_.encode()) <= MAX_ACTION_BYTES:
            rows_.append([Button(label("follow", lang, q=name), action_)])
    return Reply(say("options", lang, t=title), buttons=rows_)


def _follow(chat: BotChatRow, row: EventRow, arg: str) -> Reply:
    """⭐ a name: it waits as the chat's pending alert for the same ✅ as any alert (never saved without it)."""
    names = _follows(row)
    if not (arg.isascii() and arg.isdigit()) or int(arg) >= len(names):
        return Reply(say("gone", chat.lang))
    chat.pending_alert = names[int(arg)]
    return Reply(
        say("follow_ask", chat.lang, q=chat.pending_alert),
        buttons=[[Button(label("follow_yes", chat.lang), ALERT_YES), Button(label("no", chat.lang), ALERT_NO)]],
    )


async def mine(conv: Conversation, chat_id: str, lang_hint: str) -> Reply:
    """/reminders: the chat's reminders and watches, each a button that deletes it."""

    def work(session: Session) -> tuple[str, list[tuple[str, str, str]], dict[str, int]]:
        chat = conv._chat(session, chat_id, lang_hint)
        rows = remind.listing(session, chat)
        return chat.lang, rows, refs.numbers(session, [r[1] for r in rows])

    lang, rows, numbers = await conv._in_db(work)
    if not rows:
        return Reply(say("mine_empty", lang))
    buttons = [
        [Button(f"🗑 {'⏰' if kind == 'reminder' else '👁'} {_short(title)}", f"{FORGET}{kind[0]}{numbers[event_id]}")]
        for kind, event_id, title in rows if event_id in numbers
    ]  # fmt: skip
    return Reply(say("mine_list", lang), buttons=buttons)


async def _forget(conv: Conversation, chat_id: str, lang: str, arg: str) -> Reply:
    kind = {"r": "reminder", "w": "watch"}.get(arg[:1])

    def work(session: Session) -> None:
        chat = conv._chat(session, chat_id, lang)
        row = refs.event(session, arg[1:])
        if kind and row is not None:
            remind.forget(session, chat, kind, row.id)

    await conv._in_db(work)
    listing = await mine(conv, chat_id, lang)
    listing.text = say("mine_deleted", lang) + "\n\n" + listing.text
    return listing
