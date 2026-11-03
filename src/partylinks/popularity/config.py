"""``popularity:`` in settings.yaml; the admin changes any of it in the config tab (config_overrides)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PopularitySettings(BaseModel):
    # A view this recent counts as "watching now".
    watching_minutes: int = Field(5, ge=1, le=120)
    # "Watching now" shows from this many (the visitor opening the page is one of them).
    min_watching: int = Field(2, ge=1)
    # View rows older than this are deleted (hourly, with retention); an event that is over takes its own at once.
    keep_days: int = Field(365, ge=1)
