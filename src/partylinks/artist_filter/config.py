"""``artist_filter:`` in settings.yaml; the admin changes any of it in the config tab (config_overrides)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ArtistFilterSettings(BaseModel):
    # A genre, category or place is an artist's when at least this share of its upcoming events has it
    # (0.5: most of them), so a DJ who once played a festival with a stand-up night is not a comedian.
    # Its most common genre / category / place always counts.
    min_share: float = Field(0.5, gt=0, le=1)
