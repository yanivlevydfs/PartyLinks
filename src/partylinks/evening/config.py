"""``evening:`` in settings.yaml; the admin changes any of it in the config tab (config_overrides)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class EveningStep(BaseModel):
    key: str  # the step's name on the page (web/src/evening i18n): drinks, dinner, late...
    genres: list[str]  # place genres that fit it (happy_hour, fine_dining, coffee_carts...)
    # before: open some time in the `hours` before the event starts; after: in the `hours` after it ends.
    when: Literal["before", "after"]
    hours: float = Field(gt=0, le=24)


class EveningSettings(BaseModel):
    enabled: bool = True
    walk_m: int = Field(1500, ge=100, le=20000)  # places this far from the event at most
    per_step: int = Field(3, ge=1, le=10)  # nearest places shown per step
    # An event with no end: assumed to last this long (the "after" steps start then).
    assumed_hours: float = Field(3, gt=0, le=24)
    # "This week at the place": the venue's other listings this many days ahead.
    venue_days: int = Field(7, ge=1, le=60)
    venue_items: int = Field(6, ge=0, le=20)
    # "A budget evening": places at this price level at most (₪ = 1 ... ₪₪₪₪ = 4); unpriced places stay.
    budget_max_level: int = Field(2, ge=1, le=4)
    steps: list[EveningStep] = Field(default_factory=list)
