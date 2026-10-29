"""The ad tables: paid placements and their daily views / clicks. On the app's one Base, so
``Database.create_schema`` creates and migrates them with the rest (partylinks.db imports this module)."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, Integer, String, Text, delete, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from partylinks.db import Base, EventRow, UTCDateTime, utcnow


class SponsoredRow(Base):
    """A paid placement, picked in the admin: in the side rails, first in the event results it
    matches, or both. No foreign key (the event table is rewritten by upserts): ``prune_sponsored``
    removes the pick once retention deletes its event. Its views and clicks stay (SponsoredStatRow)."""

    __tablename__ = "sponsored"

    event_id: Mapped[str] = mapped_column(Text, primary_key=True)
    side: Mapped[str] = mapped_column(String(8))  # right | left (physical, not RTL start/end)
    position: Mapped[int] = mapped_column(Integer, default=0)  # order within its side
    until: Mapped[datetime | None] = mapped_column(UTCDateTime)  # campaign end; None = while the event is on
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    # rails | results | both; picks from before results existed are rails (the column's default).
    placement: Mapped[str] = mapped_column(String(8), default="rails")


class SponsoredStatRow(Base):
    """One sponsored event's views and clicks on one day (site time zone), for the advertiser report.
    Keeps the title so the report outlives retention deleting the event."""

    __tablename__ = "sponsored_stats"

    event_id: Mapped[str] = mapped_column(Text, primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    views: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    # The part of views / clicks from the top of the event results (the rest: the rails).
    results_views: Mapped[int] = mapped_column(Integer, default=0)
    results_clicks: Mapped[int] = mapped_column(Integer, default=0)


def prune_sponsored(session: Session) -> None:
    """Called by ``db.delete_events``: a pick whose event is gone (ended, deleted by retention) leaves the
    rails and the admin list, and its slot is free again. Also clears picks orphaned before this existed."""
    session.execute(delete(SponsoredRow).where(SponsoredRow.event_id.not_in(select(EventRow.id))))
