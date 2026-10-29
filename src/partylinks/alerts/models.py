"""The alert tables. On the app's one Base, so ``Database.create_schema`` creates them
(partylinks.db imports this module)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, delete
from sqlalchemy.orm import Mapped, Session, mapped_column

from partylinks.db import Base, NullableJSON, UTCDateTime, utcnow


class AlertRow(Base):
    """A saved search: ``criteria`` is alerts.criteria.Criteria as JSON."""

    __tablename__ = "event_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(Text)
    criteria: Mapped[dict[str, Any]] = mapped_column(NullableJSON)
    criteria_hash: Mapped[str] = mapped_column(String(64), index=True)
    instant: Mapped[bool] = mapped_column(Boolean, default=False)  # email each new match (at most hourly)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class AlertHitRow(Base):
    """This event matched this alert. The key keeps an event from being sent twice; ``seen_at`` is the bell."""

    __tablename__ = "alert_hits"

    alert_id: Mapped[int] = mapped_column(ForeignKey("event_alerts.id", ondelete="CASCADE"), primary_key=True)
    event_id: Mapped[str] = mapped_column(Text, primary_key=True, index=True)  # no FK: delete_hits removes it
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    matched_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    instant_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    digest_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class AlertStateRow(Base):
    """One row: where the matcher stopped. Events first seen after ``cursor`` are new."""

    __tablename__ = "alert_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # always 1
    cursor: Mapped[datetime] = mapped_column(UTCDateTime)


def delete_hits(session: Session, event_ids: list[str]) -> None:
    """Called by ``db.delete_events``: matches go with their event (never past events)."""
    session.execute(delete(AlertHitRow).where(AlertHitRow.event_id.in_(event_ids)))
