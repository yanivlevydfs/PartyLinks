"""The ad tables: paid placements and their daily views / clicks. On the app's one Base, so
``Database.create_schema`` creates and migrates them with the rest (partylinks.db imports this module)."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from partylinks.db import Base, UTCDateTime, utcnow


class SponsoredRow(Base):
    """A paid placement in the home page's side rails, picked in the admin. No foreign key:
    retention may delete the event, and the API then simply stops showing it."""

    __tablename__ = "sponsored"

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    side: Mapped[str] = mapped_column(String(8))  # right | left (physical, not RTL start/end)
    position: Mapped[int] = mapped_column(Integer, default=0)  # order within its side
    until: Mapped[datetime | None] = mapped_column(UTCDateTime)  # campaign end; None = while the event is on
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class SponsoredStatRow(Base):
    """One sponsored event's views and clicks on one day (site time zone), for the advertiser report.
    Keeps the title so the report outlives retention deleting the event."""

    __tablename__ = "sponsored_stats"

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    views: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
