"""The ``contact_messages`` table. On the app's one Base, so ``Database.create_schema`` creates it
(partylinks.db imports this module)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from partylinks.db import Base, UTCDateTime, utcnow


class ContactMessageRow(Base):
    """One message from the /contact form, kept until the admin deletes it."""

    __tablename__ = "contact_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text)
    email: Mapped[str] = mapped_column(Text)
    subject: Mapped[str | None] = mapped_column(Text)
    page: Mapped[str | None] = mapped_column(Text)  # the link they mention
    message: Mapped[str] = mapped_column(Text)
    lang: Mapped[str] = mapped_column(Text, default="he")
    status: Mapped[str] = mapped_column(Text, default="new")  # new | answered | closed
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
