"""The ``promote_requests`` table. On the app's one Base, so ``Database.create_schema`` creates it
(partylinks.db imports this module)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from partylinks.db import Base, JSONType, UTCDateTime, utcnow


class PromoteRequestRow(Base):
    """One request from the /promote form. The events are kept as they were when it was sent (id, title,
    date, venue, slug): an event that has passed or left the site still reads right in the admin."""

    __tablename__ = "promote_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text)
    business: Mapped[str | None] = mapped_column(Text)  # production / venue / company, optional
    email: Mapped[str] = mapped_column(Text)
    phone: Mapped[str] = mapped_column(Text)  # E.164, e.g. +972525381648
    events: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)
    placements: Mapped[list[str]] = mapped_column(JSONType, default=list)  # routes.PLACEMENTS keys
    message: Mapped[str | None] = mapped_column(Text)
    lang: Mapped[str] = mapped_column(Text, default="he")
    status: Mapped[str] = mapped_column(Text, default="new")  # routes.STATUSES
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
