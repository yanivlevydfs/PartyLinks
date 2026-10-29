"""The mail tables. On the app's one Base, so ``Database.create_schema`` creates them
(partylinks.db imports this module). No personal data: counts and switches only."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from partylinks.db import Base, UTCDateTime, utcnow

KINDS = ("welcome", "instant", "digest")


class EmailRunRow(Base):
    """One send run, for the admin: how many went, failed or were skipped."""

    __tablename__ = "email_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(10), index=True)  # welcome | instant | digest | test
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    sent: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)  # the first error, email addresses scrubbed


class MailSwitchesRow(Base):
    """One row: the admin's on / off for each kind of email (the worker reads it before every run)."""

    __tablename__ = "mail_switches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # always 1
    welcome: Mapped[bool] = mapped_column(Boolean, default=True)
    instant: Mapped[bool] = mapped_column(Boolean, default=True)
    digest: Mapped[bool] = mapped_column(Boolean, default=True)
