"""``holiday_pages:`` in settings.yaml; the admin changes any of it in the config tab (config_overrides)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class HolidayPagesSettings(BaseModel):
    enabled: bool = True
    # Holidays listed (/holidays) and with a page: those starting within this many days.
    ahead_days: int = Field(365, ge=7, le=730)
    # A holiday page needs this many upcoming events in its dates (fewer: thin content, no page).
    min_events: int = Field(3, ge=1)
    # The home row of the next holiday shows from this many days before it starts.
    rail_days: int = Field(21, ge=0, le=90)
