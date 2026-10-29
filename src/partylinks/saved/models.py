"""The saved-events table. On the app's one Base, so ``Database.create_schema`` creates it
(partylinks.db imports this module)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Text, delete
from sqlalchemy.orm import Mapped, Session, mapped_column

from partylinks.db import Base, UTCDateTime, utcnow


class SavedEventRow(Base):
    """A saved event (the save bell). No foreign key to events (like plans): ``delete_saved`` removes it with its event."""

    __tablename__ = "saved_events"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    event_id: Mapped[str] = mapped_column(Text, primary_key=True, index=True)
    saved_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


def delete_saved(session: Session, event_ids: list[str]) -> None:
    """Called by ``db.delete_events``: saved events go with their event (never past events)."""
    session.execute(delete(SavedEventRow).where(SavedEventRow.event_id.in_(event_ids)))
