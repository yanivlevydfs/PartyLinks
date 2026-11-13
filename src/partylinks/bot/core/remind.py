"""⏰ reminders and 👁 watches: set from a card's buttons, sent by the worker's bot job. Both are per chat
and per event, both end with the event (``prune``), and both are sent at most once (marked first)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from partylinks.bot.models import BotChatRow, BotReminderRow, BotWatchRow
from partylinks.db import Database, EventRow, upcoming_filter, utcnow


@dataclass
class Note:
    """One message to send: a chat, an event, and why (``hours`` before / a watch's change)."""

    chat_ref: int
    channel: str
    chat_id: str
    lang: str
    event_id: str
    kind: str  # reminder | price_drop | sold_out | back
    hours: float | None = None
    price: Decimal | None = None
    was: Decimal | None = None
    currency: str = "ILS"


# ---------------------------------------------------------------------- from the buttons
def toggle_reminder(
    session: Session, chat: BotChatRow, row: EventRow, hours: list[float], cap: int, min_ahead: timedelta
) -> tuple[str, list[float]]:
    """⏰ pressed: the reminders at ``hours`` before the start still ahead, or, pressed again, none.
    Returns (phrase key, the hours set)."""
    mine = (BotReminderRow.chat_ref == chat.id, BotReminderRow.event_id == row.id, BotReminderRow.sent_at.is_(None))
    if session.scalar(select(func.count()).select_from(BotReminderRow).where(*mine)):
        session.execute(delete(BotReminderRow).where(*mine))
        return "reminder_off", []
    if row.start_at is None:
        return "reminder_soon", []
    now = utcnow()
    ahead = sorted({h for h in hours if row.start_at - timedelta(hours=h) > now + min_ahead}, reverse=True)
    if not ahead:
        return "reminder_soon", []
    events = session.scalar(
        select(func.count(BotReminderRow.event_id.distinct())).where(BotReminderRow.chat_ref == chat.id, BotReminderRow.sent_at.is_(None))
    )  # fmt: skip
    if (events or 0) >= cap:
        return "reminder_full", []
    session.add_all(BotReminderRow(chat_ref=chat.id, event_id=row.id, remind_at=row.start_at - timedelta(hours=h)) for h in ahead)
    return "reminder_set", ahead


def toggle_watch(session: Session, chat: BotChatRow, row: EventRow, cap: int) -> str:
    """👁 pressed: watch the event's price and tickets, or, pressed again, stop."""
    mine = (BotWatchRow.chat_ref == chat.id, BotWatchRow.event_id == row.id)
    if session.scalar(select(BotWatchRow.id).where(*mine)) is not None:
        session.execute(delete(BotWatchRow).where(*mine))
        return "watch_off"
    if (session.scalar(select(func.count()).select_from(BotWatchRow).where(BotWatchRow.chat_ref == chat.id)) or 0) >= cap:
        return "watch_full"
    session.add(BotWatchRow(chat_ref=chat.id, event_id=row.id, last_price=row.group_min_price, last_sold_out=row.is_sold_out))
    return "watch_set"


def listing(session: Session, chat: BotChatRow) -> list[tuple[str, str, str]]:
    """/reminders: (kind, event id, title) of the chat's reminders and watches, soonest first."""
    reminded = session.execute(
        select(EventRow.id, EventRow.title, EventRow.start_at).join(BotReminderRow, BotReminderRow.event_id == EventRow.id)
        .where(BotReminderRow.chat_ref == chat.id, BotReminderRow.sent_at.is_(None), upcoming_filter()).distinct()
    ).all()  # fmt: skip
    watched = session.execute(
        select(EventRow.id, EventRow.title, EventRow.start_at).join(BotWatchRow, BotWatchRow.event_id == EventRow.id)
        .where(BotWatchRow.chat_ref == chat.id, upcoming_filter())
    ).all()  # fmt: skip
    rows = [("reminder", *r) for r in reminded] + [("watch", *r) for r in watched]
    rows.sort(key=lambda r: (r[3] or datetime.max.replace(tzinfo=utcnow().tzinfo), r[0]))
    return [(kind, str(event_id), str(title)) for kind, event_id, title, _ in rows]


def forget(session: Session, chat: BotChatRow, kind: str, event_id: str) -> None:
    if kind == "reminder":
        session.execute(delete(BotReminderRow).where(BotReminderRow.chat_ref == chat.id, BotReminderRow.event_id == event_id))
    else:
        session.execute(delete(BotWatchRow).where(BotWatchRow.chat_ref == chat.id, BotWatchRow.event_id == event_id))


# ---------------------------------------------------------------------- the job
_CHAT = (BotChatRow.id, BotChatRow.channel, BotChatRow.chat_id, BotChatRow.lang)


def due_reminders(db: Database) -> list[Note]:
    """Reminders whose time came, one per chat and event (the latest offset), marked sent here: at most
    once. Those of a blocked chat or an event that is over are dropped."""
    now = utcnow()
    with db.session() as session:
        rows = session.execute(
            select(*_CHAT, BotReminderRow.id, BotReminderRow.event_id, BotReminderRow.remind_at, EventRow.start_at)
            .join(BotChatRow, BotChatRow.id == BotReminderRow.chat_ref)
            .join(EventRow, EventRow.id == BotReminderRow.event_id)
            .where(BotReminderRow.sent_at.is_(None), BotReminderRow.remind_at <= now, BotChatRow.blocked_at.is_(None),
                   upcoming_filter(), EventRow.start_at > now)
            .order_by(BotReminderRow.remind_at)
        ).all()  # fmt: skip
        if not rows:
            return []
        session.execute(update(BotReminderRow).where(BotReminderRow.id.in_([r[4] for r in rows])).values(sent_at=now))
    notes: dict[tuple[int, str], Note] = {}
    for chat_ref, channel, chat_id, lang, _, event_id, _at, start_at in rows:
        hours = (start_at - now).total_seconds() / 3600 if start_at else 0.0  # what is left (a late pass says so truly)
        notes[(chat_ref, event_id)] = Note(chat_ref, channel, chat_id, lang, event_id, "reminder", hours=hours)
    return list(notes.values())


def due_watches(db: Database, min_drop_percent: float) -> list[Note]:
    """Watched events whose price dropped (by at least ``min_drop_percent``), that sold out, or whose tickets
    are back. What the chat is told becomes its ``last_*`` here, before sending: at most once."""
    notes: list[Note] = []
    with db.session() as session:
        rows = session.execute(
            select(*_CHAT, BotWatchRow, EventRow.group_min_price, EventRow.is_sold_out, EventRow.currency)
            .join(BotChatRow, BotChatRow.id == BotWatchRow.chat_ref)
            .join(EventRow, EventRow.id == BotWatchRow.event_id)
            .where(BotChatRow.blocked_at.is_(None), upcoming_filter())
        ).all()  # fmt: skip
        for chat_ref, channel, chat_id, lang, watch, price, sold_out, currency in rows:
            kind = None
            if sold_out and not watch.last_sold_out:
                kind = "sold_out"
            elif watch.last_sold_out and not sold_out:
                kind = "back"
            elif (price is not None and watch.last_price is not None
                  and price <= watch.last_price * Decimal(1 - min_drop_percent / 100) and price < watch.last_price):  # fmt: skip
                kind = "price_drop"
            if kind:
                notes.append(Note(chat_ref, channel, chat_id, lang, watch.event_id, kind, price=price, was=watch.last_price, currency=currency))
            if kind or (price is not None and price != watch.last_price):  # a rise too: the next drop counts from it
                watch.last_price, watch.last_sold_out = price, sold_out
    return notes


def prune(db: Database) -> int:
    """Reminders and watches of events that are over (or gone), and reminders sent, leave the tables."""
    with db.session() as session:
        upcoming = select(EventRow.id).where(upcoming_filter())
        a: Any = session.execute(delete(BotReminderRow).where(BotReminderRow.event_id.not_in(upcoming) | BotReminderRow.sent_at.is_not(None)))
        b: Any = session.execute(delete(BotWatchRow).where(BotWatchRow.event_id.not_in(upcoming)))
        return (a.rowcount or 0) + (b.rowcount or 0)
