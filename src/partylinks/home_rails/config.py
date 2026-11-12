"""``home_rails:`` in settings.yaml; the admin changes any of it in the config tab (config_overrides)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, field_validator

if TYPE_CHECKING:
    from partylinks.config import EnrichmentSettings


class HomeRail(BaseModel):
    key: str = Field(min_length=1)  # the web's title is t.rails[key]; a key without one is not shown
    genre: list[str] = Field(default_factory=list)  # any of these (and their sub-genres)
    exclude: list[str] = Field(default_factory=list)  # none of these (and their sub-genres)
    days: int = Field(30, ge=1, le=365)  # from now to this many days ahead
    multi: bool = False  # only events on more than one ticket site
    today: bool = False  # only today's
    time_slot: list[str] = Field(default_factory=list)  # enrichment.time_slots keys
    free_entry: bool = False  # only events with a free tier


class HomeRailsSettings(BaseModel):
    # In screen order.
    rows: list[HomeRail] = Field(default_factory=list)
    # Cards per row's pool (the page shows a random dozen of them).
    pool: int = Field(30, ge=3, le=100)

    @field_validator("rows")
    @classmethod
    def _unique_keys(cls, rows: list[HomeRail]) -> list[HomeRail]:
        keys = [r.key for r in rows]
        if dupes := sorted({k for k in keys if keys.count(k) > 1}):
            raise ValueError(f"row keys used twice: {', '.join(dupes)}")
        return rows

    def unknown_genres(self, enrichment: EnrichmentSettings) -> list[str]:
        """Genre names in the rows that are no genre, family or group (a typo would empty its row)."""
        known = {*enrichment.genres, *enrichment.declared_genres, *enrichment.genre_families, *enrichment.genre_groups}
        return sorted({g for r in self.rows for g in (*r.genre, *r.exclude) if g not in known})
