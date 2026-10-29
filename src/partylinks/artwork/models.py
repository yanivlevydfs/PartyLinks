"""The generated-artwork table. On the app's one Base, so ``Database.create_schema`` creates it
(partylinks.db imports this module)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Integer, LargeBinary, String, Text, delete
from sqlalchemy.orm import Mapped, Session, mapped_column

from partylinks.db import Base, UTCDateTime, utcnow


class EventArtworkRow(Base):
    """A drawn card for an event with no picture at all (never instead of a real one). Kept in the database
    (~700 events, ~80 KB each): the web and the worker both read it, nothing to sync on disk."""

    __tablename__ = "event_artwork"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)  # /artwork/{id}
    event_id: Mapped[str] = mapped_column(Text, unique=True)  # the group's primary listing; no FK, like plans
    signature: Mapped[str] = mapped_column(String(40))  # what was drawn: a change redraws it
    webp_1280: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    webp_640: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    made_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


def delete_artwork(session: Session, event_ids: list[str]) -> None:
    """Called by ``db.delete_events``: the card goes with its event."""
    session.execute(delete(EventArtworkRow).where(EventArtworkRow.event_id.in_(event_ids)))
