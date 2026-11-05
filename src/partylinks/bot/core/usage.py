"""What the bot did, counted for the admin's bot tab: messages, searches (and those that found nothing),
button presses, alerts / reminders / watches set and sent, digests, channel posts, ticket clicks. A chat is
a hash: the tab counts people, never names one."""

from __future__ import annotations

import hashlib
from collections import Counter
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from partylinks.bot.models import (
    BotAlertRow,
    BotChannelPostRow,
    BotChatRow,
    BotReminderRow,
    BotUsageRow,
    BotWatchRow,
)  # fmt: skip
from partylinks.db import ClickRow, utcnow

PLACEMENT = "tg"  # card.PLACEMENT: ticket clicks from the bot's cards
Q_MAX = 100


def tag(chat_id: int | str) -> str:
    """A chat's id -> what logs and usage rows carry instead."""
    return hashlib.sha256(str(chat_id).encode()).hexdigest()[:10]


def record(
    session: Session, channel: str, chat_id: str | None, kind: str, q: str | None = None, results: int | None = None
) -> None:  # fmt: skip
    session.add(BotUsageRow(
        channel=channel, chat=tag(chat_id) if chat_id else None, kind=kind, q=q[:Q_MAX] if q else None, results=results,
    ))  # fmt: skip


def forget(session: Session, chat_id: str) -> None:
    """/stop: the chat's rows stay counted, no longer as theirs."""
    session.execute(update(BotUsageRow).where(BotUsageRow.chat == tag(chat_id)).values(chat=None))


def prune(session: Session, days: int) -> int:
    result: Any = session.execute(delete(BotUsageRow).where(BotUsageRow.at < utcnow() - timedelta(days=days)))
    return result.rowcount or 0


def stats(session: Session, timezone: str, days: int = 30, top: int = 30) -> dict[str, Any]:
    """The bot tab: counts over the last ``days`` days (days in the site's time zone, oldest first), and
    what stands now (chats, alerts, reminders, watches, digest subscribers)."""
    # ponytail: counts in Python over the window's rows, like clicks.stats; SQL GROUP BY once that's millions.
    zone = ZoneInfo(timezone)
    since = utcnow() - timedelta(days=days)
    rows = session.execute(
        select(BotUsageRow.at, BotUsageRow.chat, BotUsageRow.kind, BotUsageRow.q, BotUsageRow.results)
        .where(BotUsageRow.at >= since).order_by(BotUsageRow.at)
    ).all()  # fmt: skip
    clicks = session.scalars(select(ClickRow.at).where(ClickRow.at >= since, ClickRow.placement == PLACEMENT)).all()
    today = datetime.now(zone).date()
    dates = [(today - timedelta(days=n)).isoformat() for n in reversed(range(days))]

    def day(at: datetime) -> str:
        return at.astimezone(zone).date().isoformat()

    messages = Counter(day(r.at) for r in rows if r.kind in ("message", "press", "location", "start"))
    searches = Counter(day(r.at) for r in rows if r.kind == "search")
    people: dict[str, set[str]] = {}
    for r in rows:
        if r.chat:
            people.setdefault(day(r.at), set()).add(r.chat)
    per_click = Counter(day(at) for at in clicks)
    asked = [r for r in rows if r.kind == "search" and r.q]
    terms = Counter(r.q for r in asked)
    misses = Counter(r.q for r in asked if not r.results)
    week = utcnow() - timedelta(days=7)
    now = {
        "chats": session.scalar(select(func.count()).select_from(BotChatRow)) or 0,
        "blocked": session.scalar(select(func.count()).select_from(BotChatRow).where(BotChatRow.blocked_at.is_not(None))) or 0,
        "active_7d": session.scalar(select(func.count()).select_from(BotChatRow).where(BotChatRow.last_at >= week)) or 0,
        "new_7d": session.scalar(select(func.count()).select_from(BotChatRow).where(BotChatRow.created_at >= week)) or 0,
        "alerts": session.scalar(select(func.count()).select_from(BotAlertRow).where(BotAlertRow.active.is_(True))) or 0,
        "reminders": session.scalar(select(func.count()).select_from(BotReminderRow).where(BotReminderRow.sent_at.is_(None))) or 0,
        "watches": session.scalar(select(func.count()).select_from(BotWatchRow)) or 0,
        "digest": session.scalar(select(func.count()).select_from(BotChatRow).where(BotChatRow.digest_on.is_(True))) or 0,
        "channel_posts": session.scalar(select(func.count()).select_from(BotChannelPostRow)) or 0,
    }  # fmt: skip
    return {
        "days": days,
        "now": now,
        "people": len({r.chat for r in rows if r.chat}),
        "per_kind": dict(Counter(r.kind for r in rows).most_common()),
        "ticket_clicks": len(clicks),
        "per_day": [
            {"date": d, "messages": messages[d], "searches": searches[d], "people": len(people.get(d, ())),
             "clicks": per_click[d]}
            for d in dates
        ],
        "top": [{"q": q, "searches": n} for q, n in terms.most_common(top)],
        "top_misses": [{"q": q, "searches": n} for q, n in misses.most_common(top)],
    }
