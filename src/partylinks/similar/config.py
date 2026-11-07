"""``similar:`` in settings.yaml; the admin changes any of it in the config tab (config_overrides)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SimilarSettings(BaseModel):
    enabled: bool = True
    # The index of upcoming lineups is rebuilt at most this often (seconds).
    ttl_seconds: int = Field(600, ge=30)
    # Artists shown per artist.
    limit: int = Field(8, ge=1, le=30)
    # Score = shared lineups x lineup + genre overlap (cosine, 0-1) x genre + shared venues (Jaccard, 0-1) x venue.
    lineup_weight: float = Field(3.0, ge=0)
    genre_weight: float = Field(2.0, ge=0)
    venue_weight: float = Field(1.0, ge=0)
    # Below this score an artist is not "like" this one.
    min_score: float = Field(0.5, ge=0)
