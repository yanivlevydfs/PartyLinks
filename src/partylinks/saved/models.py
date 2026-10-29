"""The saved-events table. On the app's one Base, so ``Database.create_schema`` creates it
(partylinks.db imports this module)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from partylinks.db import Base, UTCDateTime, utcnow


class SavedEventRow(Base):
    """A heart. No foreign key to events (like plans): ``delete_saved`` removes it with its event."""

    __tablename__ = "saved_events"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    event_id: Mapped[str] = mapped_column(Text, primary_key=True, index=True)
    saved_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
