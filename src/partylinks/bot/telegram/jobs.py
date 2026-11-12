"""The worker's bot jobs, on Telegram. Every ``bot.alert_interval_minutes``: tidy up, match new events
against the chats' alerts, then send what is due (alerts, ⏰ reminders, 👁 watch news). Weekly: the 📬
digest. Daily: the broadcast channel's post (phase 2). Nothing when the bot is off or has no token.

Every message is at most once: what is due is marked sent before it goes out, so one cut short (a timeout
after delivery, a refused card) is not sent again from its start every pass."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from telegram.error import Forbidden, TelegramError
from telegram.ext import AIORateLimiter, ExtBot

from partylinks.bot.core import alerts, channel, consent, daily, digest, refs, remind, usage
from partylinks.bot.core.card import cards, money
from partylinks.bot.core.phrases import duration, label, say
from partylinks.bot.core.reply import Card, Reply
from partylinks.bot.telegram import render
from partylinks.bot.telegram.handlers import CHANNEL, chat_tag

if TYPE_CHECKING:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from partylinks.config import Settings
    from partylinks.db import Database
    from partylinks.service import ScrapeService

log = logging.getLogger(__name__)
JOB_ID, DIGEST_JOB_ID, CHANNEL_JOB_ID, DAILY_JOB_ID = "bot:alerts", "bot:digest", "bot:channel", "bot:daily"
CHANNEL_LANG = "he"


async def _cards(settings: Settings, db: Database, ids: list[str], lang: str) -> list[Card]:
    """The upcoming events of ``ids`` as cards, in that order (with fresh weather)."""

    def rows() -> tuple[list, dict]:
        with db.session() as session:
            found = alerts.events(session, ids)
            return found, refs.extras(session, found)

    found, extras = await asyncio.to_thread(rows)
    return await cards(found, extras, settings, lang) if found else []


async def _deliver(db: Database, bot: ExtBot, chat_ref: int, chat_id: str, reply: Reply, lang: str, kind: str) -> bool:
    """Sends ``reply``; a chat that blocked the bot is marked. Returns whether it went out."""
    try:
        await render.send(bot, chat_id, reply, lang)
    except Forbidden:  # blocked the bot, or deleted their account
        log.info("bot: chat %s blocked the bot", chat_tag(chat_id))
        await asyncio.to_thread(alerts.mark_blocked, db, chat_ref)
        await _use(db, chat_id, "blocked")
        return False
    except TelegramError as exc:
        log.warning("bot: %s to chat %s not sent: %s", kind, chat_tag(chat_id), exc)
        return False
    await _use(db, chat_id, kind)
    return True


async def _use(db: Database, chat_id: str | None, kind: str) -> None:
    def work() -> None:
        with db.session() as session:
            usage.record(session, CHANNEL, chat_id, kind)

    await asyncio.to_thread(work)


async def send_due(settings: Settings, db: Database, bot: ExtBot) -> int:
    """Every due alert message; returns how many went out."""
    sent = 0
    for message in await asyncio.to_thread(alerts.due, settings, db):
        if message.channel != CHANNEL:
            continue
        found = await _cards(settings, db, message.event_ids, message.lang)
        await asyncio.to_thread(alerts.mark_sent, db, message)
        if not found:
            continue
        reply = Reply(say("alert_new", message.lang, q=message.query), found)
        sent += await _deliver(db, bot, message.chat_ref, message.chat_id, reply, message.lang, "alert_sent")
    return sent


async def send_reminders(settings: Settings, db: Database, bot: ExtBot) -> int:
    sent = 0
    for note in await asyncio.to_thread(remind.due_reminders, db):
        if note.channel != CHANNEL:
            continue
        found = await _cards(settings, db, [note.event_id], note.lang)
        if not found:
            continue
        text = say("reminder_now", note.lang, t=found[0].title, h=duration(note.hours or 0, note.lang))
        sent += await _deliver(db, bot, note.chat_ref, note.chat_id, Reply(text, found), note.lang, "reminder_sent")
    return sent


async def send_watches(settings: Settings, db: Database, bot: ExtBot) -> int:
    sent = 0
    for note in await asyncio.to_thread(remind.due_watches, db, settings.bot.watch_min_drop_percent):
        if note.channel != CHANNEL:
            continue
        found = await _cards(settings, db, [note.event_id], note.lang)
        if not found:
            continue
        price = money(note.price, note.currency) if note.price is not None else ""
        was = money(note.was, note.currency) if note.was is not None else ""
        text = say(f"watch_{note.kind}", note.lang, t=found[0].title, p=price, was=was)
        sent += await _deliver(db, bot, note.chat_ref, note.chat_id, Reply(text, found), note.lang, "watch_sent")
    return sent


async def _with_bot(service: ScrapeService, name: str, work: Callable[[ExtBot], Awaitable[None]]) -> None:
    settings = service.settings
    token = settings.bot.telegram_token
    if not settings.bot.enabled or token is None:
        return
    try:
        async with ExtBot(token.get_secret_value(), rate_limiter=AIORateLimiter(max_retries=2)) as bot:
            await work(bot)
    except Exception:
        log.exception("bot %s failed", name)


def _tidy(settings: Settings, db: Database) -> None:
    consent.prune(settings, db)  # first: an inactive chat's alerts and reminders go with it
    alerts.prune(db)
    remind.prune(db)
    channel.prune(db)
    with db.session() as session:
        refs.prune(session)
        usage.prune(session, settings.bot.usage_days)


async def run(service: ScrapeService) -> None:
    settings, db = service.settings, service.db

    async def work(bot: ExtBot) -> None:
        await asyncio.to_thread(_tidy, settings, db)
        await asyncio.to_thread(alerts.match, settings, db)
        counts = [await send(settings, db, bot) for send in (send_due, send_reminders, send_watches)]
        if any(counts):
            log.info("bot: %d alert, %d reminder, %d watch messages sent", *counts)

    await _with_bot(service, "alerts", work)


async def send_picks(
    settings: Settings, db: Database, bot: ExtBot, items: list[digest.Digest], intro: Callable[[digest.Digest], str], kind: str,
) -> int:  # fmt: skip
    """The 📬 digest's or the ☀️ daily tip's picks, each to its chat; returns how many went out."""
    sent = 0
    for item in items:
        if item.channel != CHANNEL:
            continue
        found = await _cards(settings, db, item.event_ids, item.lang)
        if found:
            sent += await _deliver(db, bot, item.chat_ref, item.chat_id, Reply(intro(item), found), item.lang, kind)
    return sent


async def _run_picks(service: ScrapeService, name: str, due: Callable[..., list[digest.Digest]], intro: Callable[[digest.Digest], str]) -> None:
    settings, db = service.settings, service.db

    async def work(bot: ExtBot) -> None:
        items = await asyncio.to_thread(due, settings, db)
        log.info("bot %s: %d sent", name, await send_picks(settings, db, bot, items, intro, f"{name}_sent"))

    await _with_bot(service, name, work)


async def run_digest(service: ScrapeService) -> None:
    await _run_picks(service, "digest", digest.due, lambda i: say("digest_new", i.lang, q=i.query or label("everything", i.lang)))


async def run_daily(service: ScrapeService) -> None:
    await _run_picks(service, "daily", daily.due, daily.intro)


async def run_channel(service: ScrapeService) -> None:
    settings, db = service.settings, service.db
    target = settings.bot.channel_id
    if not target:
        return

    async def work(bot: ExtBot) -> None:
        ids = await asyncio.to_thread(channel.due, settings, db, target)
        found = await _cards(settings, db, ids, CHANNEL_LANG)
        if not found:
            return
        # A channel's cards have links only: their ⏰ 👁 buttons would answer in the channel, not to a person.
        found = [Card(c.event_id, c.title, c.lines, c.photo_url, c.buttons) for c in found]
        text = say(f"channel_{channel.when(settings)}", CHANNEL_LANG)
        try:
            await render.send(bot, target, Reply(text, found), CHANNEL_LANG)
        except TelegramError as exc:  # the bot is not the channel's admin, a wrong id
            log.warning("bot channel %s: not posted: %s", target, exc)
            return
        await _use(db, None, "channel_post")

    await _with_bot(service, "channel", work)


def schedule(scheduler: AsyncIOScheduler, settings: Settings, service: ScrapeService) -> None:
    """Called by ``scheduler.build_scheduler``; nothing when the bot is off or has no token."""
    cfg = settings.bot
    if not cfg.enabled or cfg.telegram_token is None:
        return
    common = {"args": (service,), "max_instances": 1, "coalesce": True, "misfire_grace_time": settings.scheduler.misfire_grace_seconds}
    scheduler.add_job(run, IntervalTrigger(minutes=cfg.alert_interval_minutes, timezone=settings.timezone), id=JOB_ID, name=JOB_ID, **common)
    weekday = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[cfg.digest_weekday]
    scheduler.add_job(
        run_digest, CronTrigger(day_of_week=weekday, hour=cfg.digest_hour, timezone=settings.timezone),
        id=DIGEST_JOB_ID, name=DIGEST_JOB_ID, **common,
    )  # fmt: skip
    scheduler.add_job(run_daily, CronTrigger(hour=cfg.daily_hour, timezone=settings.timezone), id=DAILY_JOB_ID, name=DAILY_JOB_ID, **common)
    if cfg.channel_id:
        scheduler.add_job(
            run_channel, CronTrigger(hour=cfg.channel_hour, timezone=settings.timezone),
            id=CHANNEL_JOB_ID, name=CHANNEL_JOB_ID, **common,
        )  # fmt: skip
