"""``ads:`` in settings.yaml; the admin changes any of it in the config tab (config_overrides)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AdsSettings(BaseModel):
    # Seconds each paid event stays in a side rail's spotlight; the two differ so the rails never flip together.
    rotate_right_seconds: float = Field(6.0, ge=2, le=60)
    rotate_left_seconds: float = Field(7.5, ge=2, le=60)
