"""``alerts:`` in settings.yaml; the admin changes any of it in the config tab (config_overrides)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AlertsSettings(BaseModel):
    bell_size: int = Field(30, ge=5, le=200)  # notifications listed in the bell
    for_you: int = Field(12, ge=1, le=60)  # events in the home "For you" row
    preview: int = Field(6, ge=1, le=30)  # events shown under the alert form's live "N events now"
