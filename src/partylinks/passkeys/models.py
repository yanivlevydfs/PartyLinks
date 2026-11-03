"""The passkeys table. On the app's one Base, so ``Database.create_schema`` creates it
(partylinks.db imports this module)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from partylinks.db import Base, UTCDateTime, utcnow


class PasskeyRow(Base):
    """A passkey of one user: the credential's id and public key, as the device made them. Deleting the
    user deletes it (ON DELETE CASCADE); the device keeps its half, which then signs in to nothing."""

    __tablename__ = "user_passkeys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    credential_id: Mapped[str] = mapped_column(String(1400), unique=True)  # base64url, as the browser sends it
    public_key: Mapped[bytes] = mapped_column(LargeBinary)  # COSE-encoded
    sign_count: Mapped[int] = mapped_column(BigInteger, default=0)  # a cloned key shows up as a count going back
    transports: Mapped[str] = mapped_column(Text, default="")  # comma-separated hints (internal, hybrid, usb...)
    name: Mapped[str] = mapped_column(String(60))  # "iPhone", "Android"...: told apart on the account page
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
