"""The ``community_events`` and ``community_uploads`` tables. On the app's one Base, so ``Database.create_schema``
creates them (partylinks.db imports this module). Deleting an account deletes its rows (ON DELETE CASCADE); its
listings go first (accounts.service.delete_user calls community.publish.forget_user)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from partylinks.db import Base, JSONType, UTCDateTime, utcnow

# pending: waits for an admin; published: listed on the site; rejected: an admin said no (reason);
# removed: taken down by an admin or because the account was blocked.
STATUSES = ("pending", "published", "rejected", "removed")


class CommunityEventRow(Base):
    """One posted event: the form as sent (``data``, schemas.EventIn), its review state and its listing's id."""

    __tablename__ = "community_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    event_id: Mapped[str | None] = mapped_column(Text)  # the listing (events.id) while published
    wants_sponsored: Mapped[bool] = mapped_column(Boolean, default=False)
    promote_request_id: Mapped[int | None] = mapped_column(Integer)  # promote_requests.id (no FK)
    reject_reason: Mapped[str | None] = mapped_column(Text)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)  # an admin's yes (trust: CommunitySettings)
    reviewed_by: Mapped[str | None] = mapped_column(String(32))  # the admin's users.id (no FK)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class CommunityUploadRow(Base):
    """One picture a user sent, stored as WebP copies under ``<media.directory>/community/`` (media.MediaStore):
    ``local_path`` is the stored file's name, as ``event_images.local_path``. Claimed by media.sweep_orphans
    while the row exists; one no event of its owner uses is deleted after ``unused_upload_hours``."""

    __tablename__ = "community_uploads"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # secrets.token_hex(16): the picture's URL
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    local_path: Mapped[str] = mapped_column(Text)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    size_bytes: Mapped[int] = mapped_column(Integer)  # the file as sent
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
