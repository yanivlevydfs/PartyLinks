"""The ``home_pins`` table: events the owner pins to the home rows. On the app's one Base, so
``Database.create_schema`` creates it (partylinks.db imports this module)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Integer, Text, delete
from sqlalchemy.orm import Mapped, Session, mapped_column

from partylinks.db import Base, JSONType, UTCDateTime, utcnow


class HomePinRow(Base):
    """One pinned event. No foreign key (as ``sponsored``): ``delete_events`` drops the pin with its event.
    Any listing of the event's duplicate group shows for it, so a new primary listing keeps the pin."""

    __tablename__ = "home_pins"

    event_id: Mapped[str] = mapped_column(Text, primary_key=True)
    rails: Mapped[list[str]] = mapped_column(JSONType, default=list)  # row keys; [] = Trending + every row it matches
    position: Mapped[int] = mapped_column(Integer, default=0)  # lower first, among pins of the same row
    until: Mapped[datetime | None] = mapped_column(UTCDateTime)  # None = until the event is over
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


def delete_pins(session: Session, event_ids: Sequence[str]) -> None:
    """Called by ``db.delete_events``: a pin goes with its event."""
    session.execute(delete(HomePinRow).where(HomePinRow.event_id.in_(event_ids)))
