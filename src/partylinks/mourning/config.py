"""``mourning:`` in settings.yaml; the admin changes any of it in the config tab (config_overrides)."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class MourningDay(BaseModel):
    he: str = Field(min_length=1)  # its Hebrew name
    # Its hours end on the day itself at this hour (24: at midnight); Memorial Day ends when Independence Day begins.
    end_hour: int = Field(24, ge=1, le=24)


class MourningSettings(BaseModel):
    enabled: bool = True
    # Its hours begin on the eve (the day before) at this hour: sunset, roughly.
    eve_hour: int = Field(18, ge=0, le=23)
    # The days (mourning.core.RULES keys: their date rules) -> their name and end.
    days: dict[str, MourningDay] = Field(default_factory=dict)
    # Party / nightlife genres (with their sub-genres): not promoted when they start in a day's hours.
    genres: list[str] = Field(default_factory=list)
    # From this many hours before a day's hours begin (0: only while they are on).
    ahead_hours: int = Field(0, ge=0, le=24 * 30)

    @field_validator("days")
    @classmethod
    def _known_days(cls, days: dict[str, MourningDay]) -> dict[str, MourningDay]:
        from partylinks.mourning.core import RULES  # here: core imports search, which imports the settings

        if unknown := sorted(set(days) - set(RULES)):
            raise ValueError(f"unknown mourning days {', '.join(unknown)} (one of {', '.join(RULES)})")
        return days
