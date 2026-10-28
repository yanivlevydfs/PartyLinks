"""The plan tables. On the app's one Base, so ``Database.create_schema`` creates them with the rest
(partylinks.db imports this module)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from partylinks.db import Base, UTCDateTime, utcnow

NAME_LENGTH = 40


class PlanRow(Base):
    """One "who's coming?" link for an event. No foreign key to events (like ``sponsored``): retention
    deletes the plan with its event (``routes.delete_plans``)."""

    __tablename__ = "plans"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)  # the link's secret: secrets.token_urlsafe(6)
    event_id: Mapped[str] = mapped_column(String(128), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class PlanVoteRow(Base):
    """One friend's answer. ``voter`` is the browser's random key (who may change it); never sent back."""

    __tablename__ = "plan_votes"
    __table_args__ = (UniqueConstraint("plan_id", "voter"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[str] = mapped_column(String(12), ForeignKey("plans.id"), index=True)
    voter: Mapped[str] = mapped_column(String(36))
    name: Mapped[str] = mapped_column(String(NAME_LENGTH))
    answer: Mapped[str] = mapped_column(String(8))  # yes | maybe | no
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    # "Which party?": the options (event ids) this friend would go to; None in a one-event plan.
    picks: Mapped[list[str] | None] = mapped_column(JSON)
    # "Who's driving?": drive (has ``seats`` free) | need (a ride) | None. Nullable, no Boolean: added later.
    ride: Mapped[str | None] = mapped_column(String(8))
    seats: Mapped[int | None] = mapped_column(Integer)


class PlanEventRow(Base):
    """The other events a plan compares ("which party on Friday?"); its own ``PlanRow.event_id`` is
    option one. Deleted with the event (``routes.delete_plans``)."""

    __tablename__ = "plan_events"

    plan_id: Mapped[str] = mapped_column(String(12), ForeignKey("plans.id"), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(128), primary_key=True, index=True)
    position: Mapped[int] = mapped_column(Integer)  # 1, 2, 3 (option one is the plan's event)
