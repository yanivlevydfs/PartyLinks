"""Short numbers for events, for the buttons under a card (Telegram's callback data is at most 64 bytes,
an event id may be longer), and the extras every card needs (``api._summary_extras`` + its number)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, defer, selectinload

from partylinks.bot.models import BotRefRow
from partylinks.db import EventRow, upcoming_filter


def numbers(session: Session, ids: list[str]) -> dict[str, int]:
    """Each event's number, made the first time it is asked for."""
    if not ids:
        return {}
    session.execute(insert(BotRefRow).values([{"event_id": i} for i in dict.fromkeys(ids)]).on_conflict_do_nothing())
    return dict(session.execute(select(BotRefRow.event_id, BotRefRow.id).where(BotRefRow.event_id.in_(ids))).all())


def extras(session: Session, rows: list[EventRow]) -> dict[str, dict[str, Any]]:
    """``api._summary_extras`` (best deal, cover) with each row's number under ``ref``."""
    from partylinks.api import _summary_extras  # local: api imports the bot

    if not rows:
        return {}
    out = _summary_extras(session, rows)
    for event_id, number in numbers(session, [r.id for r in rows]).items():
        out.setdefault(event_id, {})["ref"] = number
    return out


def event(session: Session, ref: str) -> EventRow | None:
    """The upcoming event behind a button's number, or None (a stale button, an event that is over)."""
    if not (ref.isascii() and ref.isdigit()):
        return None
    return session.scalar(
        select(EventRow).join(BotRefRow, BotRefRow.event_id == EventRow.id)
        .where(BotRefRow.id == int(ref), upcoming_filter())
        .options(selectinload(EventRow.images), defer(EventRow.description))
    )  # fmt: skip


def prune(session: Session) -> int:
    """Numbers of events that are over (or gone) leave the table: their buttons answer "no longer available"."""
    result: Any = session.execute(delete(BotRefRow).where(BotRefRow.event_id.not_in(select(EventRow.id).where(upcoming_filter()))))
    return result.rowcount or 0
