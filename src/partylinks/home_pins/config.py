"""``home_pins:`` in settings.yaml; the admin changes it in the config tab (config_overrides)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class HomePinsSettings(BaseModel):
    events: list[str] = Field(default_factory=list)  # slugs or ids, first is shown first
