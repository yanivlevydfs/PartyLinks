"""``artwork:`` in settings.yaml; the admin changes any of it in the config tab (config_overrides)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ArtworkSettings(BaseModel):
    # Cards drawn per pass at most (~0.1 s each, 4 at a time); the rest wait for the next pass.
    per_pass: int = Field(1500, ge=1, le=100_000)
    # Minutes between the worker's artwork passes. Read when the scheduler starts.
    interval_minutes: float = Field(15, gt=0, le=1440)
