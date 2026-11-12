"""``smart_rails:`` in settings.yaml; the admin changes any of it in the config tab (config_overrides)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SmartRailsSettings(BaseModel):
    enabled: bool = True
    # The home rows these come after (a home_rails row key); missing: they lead.
    after: str = "tonight"
    # Cards per row's pool (the page shows a random dozen of them).
    pool: int = Field(30, ge=3, le=100)
    # "Free if you come now": still free, and the free entry ends within this many hours.
    free_now_hours: float = Field(4, gt=0, le=24)
    # "Revealed today": first seen this many hours ago or less.
    new_hours: float = Field(24, gt=0, le=168)
    # "Price dropped": a listing of the event got cheaper this many days ago or less.
    price_down_days: float = Field(3, gt=0, le=30)
    # Weather rows: tonight's forecast at this city (enrichment.cities key), the slot that covers this hour.
    weather_city: str = "tel-aviv"
    weather_hour: int = Field(20, ge=0, le=23)
    # "Rainy day" from this much rain (mm) in that slot: the indoor genres' events tonight.
    rain_mm: float = Field(0.5, ge=0)
    indoor_genres: list[str] = Field(default_factory=list)
    # "Perfect evening outside": dry, between these temperatures (°C): the outdoor genres' events tonight.
    outdoor_min_c: float = 18
    outdoor_max_c: float = 32
    outdoor_genres: list[str] = Field(default_factory=list)
