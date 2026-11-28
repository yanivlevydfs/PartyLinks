"""``holiday_pages:`` in settings.yaml; the admin changes any of it in the config tab (config_overrides)."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class HolidaySeason(BaseModel):
    """A holiday whose page and rows list its own events (this genre) through its season, not every event on its
    dates (Christmas markets and concerts run all of December)."""

    genre: str
    # Same days every year ("MM-DD"); none: the holiday's own dates (search.DATED, the Hebrew calendar).
    start: str | None = None
    end: str | None = None

    @field_validator("start", "end")
    @classmethod
    def _month_day(cls, v: str | None) -> str | None:
        if v is not None:
            month, day = (int(p) for p in v.split("-"))
            if not (1 <= month <= 12 and 1 <= day <= 31):
                raise ValueError(f"{v!r} is not MM-DD")
        return v


class HolidayPagesSettings(BaseModel):
    enabled: bool = True
    # Holidays listed (/holidays) and with a page: those starting within this many days.
    ahead_days: int = Field(365, ge=7, le=730)
    # A holiday page needs this many upcoming events in its dates (fewer: thin content, no page).
    min_events: int = Field(3, ge=1)
    # The home row of the next holiday shows from this many days before it starts.
    rail_days: int = Field(21, ge=0, le=90)
    # Season holidays: key (search.HOLIDAY_NAMES) -> its genre and, when fixed, its days.
    seasons: dict[str, HolidaySeason] = Field(default_factory=dict)
    # Each holiday's icon (the bot's holiday button); others get a generic one.
    icons: dict[str, str] = Field(default_factory=dict)
    # Each holiday's drawn picture (web/public/holidays/<name>.webp and <name>-og.webp, scripts/holiday_art.py):
    # its row's banner and its page's sharing picture.
    art: dict[str, str] = Field(default_factory=dict)
