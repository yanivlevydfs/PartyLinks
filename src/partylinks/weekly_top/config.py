"""``weekly_top:`` in settings.yaml; the admin changes any of it in the config tab (config_overrides)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class WeeklyTopSettings(BaseModel):
    enabled: bool = True
    days: int = Field(7, ge=1, le=31)  # events in the next this many days
    views_days: int = Field(7, ge=1, le=60)  # page views counted over the last this many days
    limit: int = Field(10, ge=3, le=50)
    # Score = likes x like + views x view + (ticket sites selling it - 1) x listing.
    like_weight: float = Field(3.0, ge=0)
    view_weight: float = Field(1.0, ge=0)
    listing_weight: float = Field(2.0, ge=0)
    # "Rising genres": the genres with the most events first seen in the last this many days.
    rising_days: int = Field(7, ge=1, le=60)
    rising_limit: int = Field(6, ge=1, le=20)
