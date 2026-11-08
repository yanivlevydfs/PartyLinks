"""Tables of place info: Ontopo's venues, their photos, and what each place (one URL) was matched to."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Float, Integer, LargeBinary, Text
from sqlalchemy.orm import Mapped, mapped_column

from partylinks.db import Base, NullableJSON, UTCDateTime, utcnow


class OntopoVenueRow(Base):
    __tablename__ = "ontopo_venues"

    slug: Mapped[str] = mapped_column(Text, primary_key=True)
    title: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    phone: Mapped[str | None] = mapped_column(Text)  # digits only, 03..., not +972
    address: Mapped[str | None] = mapped_column(Text)
    website: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str] | None] = mapped_column(NullableJSON)  # cuisine: ["סושי", "אסייאתי"]
    hours: Mapped[dict[str, list[str]] | None] = mapped_column(NullableJSON)  # {"Sunday": ["12:00-00:00"]}
    photos: Mapped[list[str] | None] = mapped_column(NullableJSON)  # Ontopo's photo URLs
    lastmod: Mapped[str | None] = mapped_column(Text)  # the sitemap's, to read a page again when it changed
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class OntopoPhotoRow(Base):
    __tablename__ = "ontopo_photos"

    slug: Mapped[str] = mapped_column(Text, primary_key=True)
    n: Mapped[int] = mapped_column(Integer, primary_key=True)
    webp: Mapped[bytes] = mapped_column(LargeBinary)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class PlaceInfoRow(Base):
    """One per place (``events.url``, which is one page per place)."""

    __tablename__ = "place_info"

    url: Mapped[str] = mapped_column(Text, primary_key=True)
    ontopo_slug: Mapped[str | None] = mapped_column(Text)
    matched_by: Mapped[str | None] = mapped_column(Text)  # link (HappyTLV's own), phone or name
    site: Mapped[dict[str, Any] | None] = mapped_column(NullableJSON)  # own website's JSON-LD: hours, phone, address
    site_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
