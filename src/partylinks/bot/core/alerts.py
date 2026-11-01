"""New events x the chats' alerts -> ``bot_alert_hits``, then what is due to send. Matching is the site's
alert matcher (``alerts.matcher``: the same new events, the same filter sets), so a bot alert finds what
the same search finds on the site."""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, defer, selectinload

from partylinks.alerts import matcher
from partylinks.alerts.criteria import Criteria
from partylinks.bot.core.understand import today_in
from partylinks.bot.models import BotAlertHitRow, BotAlertRow, BotChatRow, BotStateRow
from partylinks.config import Settings
from partylinks.db import Database, EventRow, upcoming_filter, utcnow

log = logging.getLogger(__name__)
INSERT_CHUNK = 5_000  # hit rows per INSERT (two parameters each)
STALE = timedelta(days=1)  # a match not sent by then is no news any more: dropped, never sent late


@dataclass
class Due:
    """One message to send: a chat, the alert, its new events (soonest first)."""

    chat_ref: int
    channel: str
    chat_id: str
    lang: str
    alert_id: int
    query: str
    event_ids: list[str]


def match(settings: Settings, db: Database) -> int:
    """One pass over the events first seen since the last one. Returns the number of new hits."""
    now = utcnow()
    upto = now - matcher.SETTLE  # read here: the site's alerts wait the same
    with db.session() as session:
        state = session.get(BotStateRow, 1)
        if state is None:  # the first pass starts now: no flood of events that were already there
            session.add(BotStateRow(id=1, cursor=upto))
            return 0
        new_ids = matcher.new_event_ids(session, state.cursor, upto)
        state.cursor = upto
        if not new_ids:
            return 0
        alerts = session.execute(
            select(BotAlertRow.id, BotAlertRow.query)
            .join(BotChatRow, BotChatRow.id == BotAlertRow.chat_ref)
            .where(BotAlertRow.active.is_(True), BotChatRow.blocked_at.is_(None))
        ).all()
        by_query: dict[str, list[int]] = defaultdict(list)
        for alert_id, query in alerts:
            by_query[query].append(alert_id)
        sets, today, rows = matcher._Sets(settings, session, new_ids), today_in(settings), []
        for query, ids in by_query.items():
            try:
                params = Criteria(q=query).params(today)
            except ValueError:  # a query the site's alerts refuse (too long): never matches
                continue
            if "q" not in params:
                continue
            rows += [{"alert_id": a, "event_id": e} for e in sets.match(params) for a in ids]
        for start in range(0, len(rows), INSERT_CHUNK):  # PostgreSQL takes at most 65,535 parameters a statement
            session.execute(insert(BotAlertHitRow).values(rows[start : start + INSERT_CHUNK]).on_conflict_do_nothing())
    log.info("bot alerts: %d new events, %d alerts, %d hits", len(new_ids), len(alerts), len(rows))
    return len(rows)


def prune(db: Database) -> int:
    """Hits of events that are over (or gone) leave the table, and so do matches not sent within STALE
    (over a cap, or the chat had blocked the bot): never sent late."""
    with db.session() as session:
        upcoming = select(EventRow.id).where(upcoming_filter())
        stale = BotAlertHitRow.sent_at.is_(None) & (BotAlertHitRow.matched_at < utcnow() - STALE)
        result: Any = session.execute(delete(BotAlertHitRow).where(BotAlertHitRow.event_id.not_in(upcoming) | stale))
        return result.rowcount or 0


def due(settings: Settings, db: Database) -> list[Due]:
    """Unsent hits per chat and alert, at most ``alert_max_events`` per message and ``alert_max_per_day``
    events per chat in any 24 hours (the soonest). A show at many dates goes once, its soonest date.
    What a cap leaves out waits for a later pass, until ``prune`` drops it as stale."""
    cfg = settings.bot
    with db.session(write=False) as session:
        pending = session.execute(
            select(BotChatRow.id, BotChatRow.channel, BotChatRow.chat_id, BotChatRow.lang,
                   BotAlertRow.id, BotAlertRow.query, EventRow.id, EventRow.title)
            .select_from(BotAlertHitRow)
            .join(BotAlertRow, BotAlertRow.id == BotAlertHitRow.alert_id)
            .join(BotChatRow, BotChatRow.id == BotAlertRow.chat_ref)
            .join(EventRow, EventRow.id == BotAlertHitRow.event_id)
            .where(BotAlertHitRow.sent_at.is_(None), BotAlertRow.active.is_(True), BotChatRow.blocked_at.is_(None), upcoming_filter())
            .order_by(BotChatRow.id, BotAlertRow.id, EventRow.start_at.asc().nulls_last(), EventRow.id)
        ).all()  # fmt: skip
        if not pending:
            return []
        chats = {p[0] for p in pending}
        sent_today = dict(session.execute(
            select(BotAlertRow.chat_ref, func.count(BotAlertHitRow.event_id.distinct()))
            .select_from(BotAlertHitRow)
            .join(BotAlertRow, BotAlertRow.id == BotAlertHitRow.alert_id)
            .where(BotAlertRow.chat_ref.in_(chats), BotAlertHitRow.sent_at >= utcnow() - timedelta(days=1))
            .group_by(BotAlertRow.chat_ref)
        ).all())  # fmt: skip
    out: dict[tuple[int, int], Due] = {}
    left = {c: cfg.alert_max_per_day - sent_today.get(c, 0) for c in chats}
    seen: dict[int, set[str]] = defaultdict(set)  # chat -> events (and show titles) already in a message
    for chat_ref, channel, chat_id, lang, alert_id, query, event_id, title in pending:
        message = out.get((chat_ref, alert_id))
        if left[chat_ref] <= 0 or event_id in seen[chat_ref] or title in seen[chat_ref]:
            continue
        if message is None:
            message = out[(chat_ref, alert_id)] = Due(chat_ref, channel, chat_id, lang, alert_id, query, [])
        if len(message.event_ids) >= cfg.alert_max_events:
            continue
        message.event_ids.append(event_id)
        seen[chat_ref] |= {event_id, title}
        left[chat_ref] -= 1
    return list(out.values())


def mark_sent(db: Database, message: Due) -> None:
    """The message's events count as sent for every alert of the chat (no event twice), and so does the rest
    of its alert's backlog: the hits over a cap are dropped, not sent late."""
    with db.session() as session:
        mine = select(BotAlertRow.id).where(BotAlertRow.chat_ref == message.chat_ref)
        session.execute(
            update(BotAlertHitRow)
            .where(
                BotAlertHitRow.sent_at.is_(None),
                (BotAlertHitRow.alert_id == message.alert_id)
                | (BotAlertHitRow.alert_id.in_(mine) & BotAlertHitRow.event_id.in_(message.event_ids)),
            )
            .values(sent_at=utcnow())
        )  # fmt: skip


def mark_blocked(db: Database, chat_ref: int) -> None:
    """The user blocked the bot: nothing more is sent until they write again, and what was waiting never is."""
    with db.session() as session:
        session.execute(update(BotChatRow).where(BotChatRow.id == chat_ref).values(blocked_at=utcnow()))
        mine = select(BotAlertRow.id).where(BotAlertRow.chat_ref == chat_ref)
        session.execute(delete(BotAlertHitRow).where(BotAlertHitRow.alert_id.in_(mine), BotAlertHitRow.sent_at.is_(None)))


def events(session: Session, ids: list[str]) -> list[EventRow]:
    """The rows of ``ids``, in that order (cards need their pictures)."""
    rows = session.scalars(
        select(EventRow).where(EventRow.id.in_(ids))
        .options(selectinload(EventRow.images), defer(EventRow.description), defer(EventRow.extra))
    ).all()  # fmt: skip
    found = {r.id: r for r in rows}
    return [found[i] for i in ids if i in found]
