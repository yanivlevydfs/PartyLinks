"""The bot's tables. On the app's one Base, so ``Database.create_schema`` creates them (partylinks.db
imports this module). A chat is never a site account: no name, no phone, only the chat's id."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, Float, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from partylinks.db import Base, UTCDateTime, utcnow


class BotChatRow(Base):
    """One private chat with the bot, and what the conversation remembers (the last search)."""

    __tablename__ = "bot_chats"
    __table_args__ = (UniqueConstraint("channel", "chat_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel: Mapped[str] = mapped_column(String(16))  # telegram (whatsapp later)
    chat_id: Mapped[str] = mapped_column(String(32))
    lang: Mapped[str] = mapped_column(String(2), default="he")
    lang_chosen: Mapped[bool] = mapped_column(Boolean, default=False)  # /lang: no more guessing from the text
    last_query: Mapped[str | None] = mapped_column(Text)  # the last search, as the words it ran with
    last_offset: Mapped[int] = mapped_column(Integer, default=0)  # cards already shown of it ("more")
    last_cheap: Mapped[bool] = mapped_column(Boolean, default=False)  # it was sorted cheapest first
    last_lat: Mapped[float | None] = mapped_column(Float)  # a shared location: the last search was "near me"
    last_lng: Mapped[float | None] = mapped_column(Float)
    last_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    pending_alert: Mapped[str | None] = mapped_column(Text)  # the search an "alert me" waits to confirm
    digest_on: Mapped[bool] = mapped_column(Boolean, default=False)  # /digest: the weekly picks (opt-in only)
    digest_query: Mapped[str | None] = mapped_column(Text)  # the digest's search words; None: everything
    digest_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    blocked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)  # the user blocked the bot: nothing is sent
    consent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)  # "I agree" on the entry screen (consent.py)
    seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime)  # last message: inactive chats are deleted


class BotAlertRow(Base):
    """A search the chat asked to be told about (an explicit button and a confirm, never on its own)."""

    __tablename__ = "bot_alerts"
    __table_args__ = (UniqueConstraint("chat_ref", "query"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_ref: Mapped[int] = mapped_column(ForeignKey("bot_chats.id", ondelete="CASCADE"), index=True)
    query: Mapped[str] = mapped_column(Text)  # the search's words: alerts.criteria.Criteria(q=query)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class BotAlertHitRow(Base):
    """This new event matched this alert. The key keeps an event from being sent twice; ``sent_at`` is
    set once it went out. No foreign key to events: the job prunes hits of events that are over."""

    __tablename__ = "bot_alert_hits"

    alert_id: Mapped[int] = mapped_column(ForeignKey("bot_alerts.id", ondelete="CASCADE"), primary_key=True)
    event_id: Mapped[str] = mapped_column(Text, primary_key=True, index=True)
    matched_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class BotStateRow(Base):
    """One row: where the bot's matcher stopped. Events first seen after ``cursor`` are new."""

    __tablename__ = "bot_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # always 1
    cursor: Mapped[datetime] = mapped_column(UTCDateTime)


class BotRefRow(Base):
    """A short number for an event: card buttons carry it (Telegram's callback data is at most 64 bytes,
    an event id may be longer). Pruned with the event."""

    __tablename__ = "bot_refs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(Text, unique=True)


class BotReminderRow(Base):
    """⏰ on a card: one row per ``bot.reminder_hours`` offset still ahead; sent once at ``remind_at``."""

    __tablename__ = "bot_reminders"
    __table_args__ = (UniqueConstraint("chat_ref", "event_id", "remind_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_ref: Mapped[int] = mapped_column(ForeignKey("bot_chats.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[str] = mapped_column(Text, index=True)
    remind_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class BotWatchRow(Base):
    """👁 on a card: told when the price drops, it sells out or tickets are back. ``last_*``: what the
    chat was last told (or saw when it started watching)."""

    __tablename__ = "bot_watches"
    __table_args__ = (UniqueConstraint("chat_ref", "event_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_ref: Mapped[int] = mapped_column(ForeignKey("bot_chats.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[str] = mapped_column(Text, index=True)
    last_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    last_sold_out: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class BotChannelPostRow(Base):
    """An event posted to a broadcast channel (phase 2): never posted there twice."""

    __tablename__ = "bot_channel_posts"

    channel: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[str] = mapped_column(Text, primary_key=True)
    posted_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class BotUsageRow(Base):
    """One thing the bot did, for the admin's bot tab. ``chat`` is a hash of the chat id (counts people,
    never names one); /stop empties it."""

    __tablename__ = "bot_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    channel: Mapped[str] = mapped_column(String(16))
    chat: Mapped[str | None] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(24))  # usage.KINDS
    q: Mapped[str | None] = mapped_column(String(100))  # a search's words
    results: Mapped[int | None] = mapped_column(Integer)
