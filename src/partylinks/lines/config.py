"""``lines:`` in settings.yaml; the admin changes any of it in the config tab (config_overrides)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class LinesSettings(BaseModel):
    enabled: bool = True
    # A line: the same title at the same place on the same weekday, on this many upcoming dates at least.
    min_dates: int = Field(2, ge=2, le=10)
