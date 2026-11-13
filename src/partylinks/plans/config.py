"""``plans:`` in settings.yaml; the admin changes any of it in the config tab (config_overrides)."""

from __future__ import annotations

from pydantic import BaseModel, Field

MAX_OPTIONS_CAP = 10  # the request schema's bound on picks; max_options stays at or under it


class PlansSettings(BaseModel):
    max_votes: int = Field(60, ge=2, le=500)  # answers one plan takes: a group of friends, not a crowd
    max_options: int = Field(4, ge=1, le=MAX_OPTIONS_CAP)  # events one plan compares ("which party on Friday?")
