"""The plan tables. On the app's one Base, so ``Database.create_schema`` creates them with the rest
(partylinks.db imports this module)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from partylinks.db import Base, NullableJSON, UTCDateTime, utcnow

NAME_LENGTH = 40
GROUP_NAME_LENGTH = 60


class PlanRow(Base):
    """One "who's coming?" link for an event. No foreign key to events (like ``sponsored``): retention
    deletes the plan with its event (``routes.delete_plans``)."""

    __tablename__ = "plans"

    id: Mapped[str] = mapped_column(String(12), primary_key=True)  # the link's secret: secrets.token_urlsafe(9), 72 bits (older links: 6)
    event_id: Mapped[str] = mapped_column(Text, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    # A group in the creator's account ("my groups"), when made signed in; renamed only by them.
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    name: Mapped[str | None] = mapped_column(String(GROUP_NAME_LENGTH))


class PlanVoteRow(Base):
    """One friend's answer. ``voter`` is the browser's random key (who may change it); never sent back."""

    __tablename__ = "plan_votes"
    __table_args__ = (UniqueConstraint("plan_id", "voter"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plan_id: Mapped[str] = mapped_column(String(12), ForeignKey("plans.id", ondelete="CASCADE"), index=True)
    voter: Mapped[str] = mapped_column(String(36))
    name: Mapped[str] = mapped_column(String(NAME_LENGTH))
    answer: Mapped[str] = mapped_column(String(8))  # yes | maybe | no
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)
    # "Which party?": the options (event ids) this friend would go to; None in a one-event plan.
    picks: Mapped[list[str] | None] = mapped_column(NullableJSON)
    # "Who's driving?": drive (has ``seats`` free) | need (a ride) | None. Nullable, no Boolean: added later.
    ride: Mapped[str | None] = mapped_column(String(8))
    seats: Mapped[int | None] = mapped_column(Integer)
    # A signed-in friend's answer: the group shows in their "my groups"; goes with their account.
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)


class PlanEventRow(Base):
    """The other events a plan compares ("which party on Friday?"); its own ``PlanRow.event_id`` is
    option one. Deleted with the event (``routes.delete_plans``)."""

    __tablename__ = "plan_events"

    plan_id: Mapped[str] = mapped_column(String(12), ForeignKey("plans.id", ondelete="CASCADE"), primary_key=True)
    event_id: Mapped[str] = mapped_column(Text, primary_key=True, index=True)
    position: Mapped[int] = mapped_column(Integer)  # 1, 2, 3 (option one is the plan's event)
