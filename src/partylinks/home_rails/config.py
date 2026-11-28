"""``home_rails:`` in settings.yaml; the admin changes any of it in the config tab (config_overrides)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Literal

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
    weekend: bool = False  # only weekend days (enrichment.weekend_days)
    abroad: bool = False  # events abroad instead of in Israel
    discount: bool = False  # only events with a discount on some ticket site
    max_price: float | None = Field(None, gt=0)  # cheapest ticket at most this much (free ones excluded)
    # Only the next dates of this holiday (a search.HOLIDAY_NAMES key: purim, new_year...), however far; `days` is ignored.
    holiday: str | None = None
    # Only the next such calendar month (6: June, this one's rest while in it), however far; `days` is ignored.
    month: int | None = Field(None, ge=1, le=12)
    # The cards' order: "trending" (on the most ticket sites first), "latest" (the latest to end first: the
    # end time, else the start plus retention.assumed_duration_hours; a place's closing hour) or "rating" (a
    # place's Google rating, the best first).
    sort: Literal["trending", "latest", "rating"] = "trending"
    # Places (bars, cafés, attractions: one listing per day they open, partylinks.placecards) only, or events only.
    places: bool | None = None
    region: list[str] = Field(default_factory=list)  # enrichment.regions keys (jerusalem, haifa, galilee, negev...)
    day_of_week: list[str] = Field(default_factory=list)  # "thursday", "friday"...: only those days
    min_age: int | None = Field(None, ge=1, le=99)  # a door policy of at least this age (events' min_age)
    # A place's own amenities ("כלבים מותרים", "מתאים לילדים") and tags ("דייט", "קוקטיילים"): any of them.
    amenity: list[str] = Field(default_factory=list)
    place_tag: list[str] = Field(default_factory=list)
    min_reviews: int | None = Field(None, ge=1)  # a place's Google reviews, at least
    min_price_level: int | None = Field(None, ge=1, le=4)  # a place's ₪ to ₪₪₪₪ as 1-4
    max_price_level: int | None = Field(None, ge=1, le=4)

    @field_validator("holiday")
    @classmethod
    def _known_holiday(cls, value: str | None) -> str | None:
        from partylinks.search import HOLIDAY_NAMES  # here: search imports the settings

        if value is not None and value not in HOLIDAY_NAMES:
            raise ValueError(f"unknown holiday {value!r} (one of {', '.join(HOLIDAY_NAMES)})")
        return value

    def cache_tag(self, today: date) -> str:
        """Its settings, and the day for a row whose dates move with it: either changing rebuilds it at once."""
        daily = self.today or self.weekend or self.holiday or self.month
        return self.model_dump_json() + (today.isoformat() if daily else "")

    def window(self, today: date) -> tuple[date, date] | None:
        """The row's own dates (its holiday's or month's next ones), or None: from now to `days` ahead."""
        from partylinks.search import _next_holiday, _next_month

        if self.holiday:  # no known dates left: an empty window (the row hides)
            return _next_holiday(today, self.holiday) or (today - timedelta(days=1), today - timedelta(days=1))
        return _next_month(today, self.month) if self.month else None


class HomeRailsSettings(BaseModel):
    # In screen order.
    rows: list[HomeRail] = Field(default_factory=list)
    # Cards per row's pool (the page shows a random dozen of them).
    pool: int = Field(30, ge=3, le=100)
    # Each row's cards are kept this long (home_rails.RowCache), however often the whole response is rebuilt...
    row_ttl_seconds: float = Field(900, ge=0)
    # ...and one rebuild refreshes at most this many stale rows (a row is ~0.5 s of queries on prod).
    rows_per_build: int = Field(10, ge=1)

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
