"""The account tables. On the app's one Base, so ``Database.create_schema`` creates them with the rest
(partylinks.db imports this module)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from partylinks.db import Base, UTCDateTime, utcnow

Gender = Literal["male", "female", "none"]  # "none": would rather not say


class UserRow(Base):
    """A Google account. ``id`` is ours (random) and the only one logs carry; ``google_sub`` and
    ``email`` are never logged."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # secrets.token_hex(16)
    google_sub: Mapped[str] = mapped_column(Text, unique=True)
    email: Mapped[str] = mapped_column(Text, unique=True)  # lowercased
    name: Mapped[str] = mapped_column(Text)
    picture: Mapped[str | None] = mapped_column(Text)
    locale: Mapped[str] = mapped_column(String(2), default="he")  # the language of the emails
    # The user's own answer (male | female | none = would rather not say), asked when joining a group;
    # None until asked. Words on the site follow it ("בא" / "באה").
    gender: Mapped[str | None] = mapped_column(String(8))
    role: Mapped[str] = mapped_column(String(10), default="user")  # user | admin
    blocked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # The weekly digest carries ticket links: advertising under the Communications Law (§30A), so it
    # needs the user's explicit yes, kept with its time.
    digest_opt_in: Mapped[bool] = mapped_column(Boolean, default=False)
    digest_opt_in_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_digest_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_instant_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    welcome_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    last_login_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class SessionRow(Base):
    """A signed-in browser. Only the SHA-256 of its cookie is kept; deleting the row signs it out."""

    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
