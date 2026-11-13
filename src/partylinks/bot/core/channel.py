"""Phase 2: the broadcast channel. Once a day the bot posts tonight's picks to ``bot.channel_id`` (on
``bot.digest_weekday``: the weekend's), never an event it posted there before."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Literal

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from partylinks.bot.core.picks import picks
from partylinks.bot.core.understand import today_in
from partylinks.bot.models import BotChannelPostRow
from partylinks.config import Settings, get_settings
from partylinks.db import Database, utcnow


def when(settings: Settings) -> Literal["weekend", "tonight"]:
    return "weekend" if today_in(settings).weekday() == settings.bot.digest_weekday else "tonight"


def due(settings: Settings, db: Database, channel: str) -> list[str]:
    """Today's picks not posted to ``channel`` yet, recorded as posted here: at most once."""
    with db.session() as session:
        posted = set(session.scalars(select(BotChannelPostRow.event_id).where(BotChannelPostRow.channel == channel)))
        rows = picks(session, settings, None, when(settings), today_in(settings), settings.bot.channel_results, exclude=posted)
        if rows:
            session.execute(insert(BotChannelPostRow).values([{"channel": channel, "event_id": r.id} for r in rows]).on_conflict_do_nothing())
        return [r.id for r in rows]


def prune(db: Database) -> int:
    with db.session() as session:
        since = utcnow() - timedelta(days=get_settings().bot.channel_keep_days)
        result: Any = session.execute(delete(BotChannelPostRow).where(BotChannelPostRow.posted_at < since))
        return result.rowcount or 0
