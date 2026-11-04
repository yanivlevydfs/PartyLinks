"""``artist_links:`` in settings.yaml; the admin changes any of it in the config tab (config_overrides)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ArtistLinksSettings(BaseModel):
    enabled: bool = True
    # MusicBrainz WS/2 (JSON). Its policy: identify the app with a contact, at most one request per second.
    url: str = "https://musicbrainz.org/ws/2"
    user_agent: str = "SPOTX/0.1 ( contact@spotx.fun )"
    min_interval_seconds: float = Field(1.1, ge=1)
    timeout_seconds: float = Field(20, gt=0)
    # Tries per request on 429 / 5xx / network errors (Retry-After honoured, else exponential backoff).
    attempts: int = Field(3, ge=1, le=6)
    backoff_seconds: float = Field(2, ge=0)
    # Requests that failed after their retries, in a row, that end the pass (MusicBrainz down: don't hammer it).
    max_consecutive_failures: int = Field(3, ge=1)
    # The worker's pass: every this many minutes, at most this many MusicBrainz requests.
    interval_minutes: int = Field(30, ge=1)
    max_lookups_per_run: int = Field(200, ge=0)
    # A found artist's links are re-read (one request by its MBID) after this many days; a miss is asked again after retry_failed_days.
    refresh_days: int = Field(90, ge=1)
    retry_failed_days: int = Field(14, ge=1)
    # A search hit counts only at this MusicBrainz score or above, and only when its name or an alias is the lineup name.
    min_score: int = Field(90, ge=0, le=100)
    # Shorter names (normalised) are never looked up: "DJ", "MC" would match anyone.
    min_name_length: int = Field(3, ge=1)
    search_limit: int = Field(10, ge=1, le=100)
    # The web process reads the found links into memory at most this often (no MusicBrainz call ever in a request).
    cache_seconds: int = Field(600, ge=0)
    # Services shown, in this order; a link to anything else is not shown.
    services: list[str] = Field(default_factory=lambda: [
        "spotify", "youtube", "apple_music", "soundcloud", "deezer", "bandcamp", "instagram", "tiktok", "facebook", "x", "website",
    ])  # fmt: skip
