"""The ``config_overrides`` table: one row per saved version of the admin's overrides; the newest is in force.
``Database.create_schema`` creates it with the rest (partylinks.db imports this module)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from partylinks.db import Base, JSONType, UTCDateTime, utcnow


class ConfigOverrideRow(Base):
    __tablename__ = "config_overrides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Sparse settings tree: only the keys the admin changed ({"retention": {"assumed_duration_hours": 4}}).
    data: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
