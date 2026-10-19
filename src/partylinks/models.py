"""Source-agnostic event model every scraper must produce."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, HttpUrl, computed_field, field_validator, model_validator


class TierStatus(StrEnum):
    ON_SALE = "on_sale"
    SOLD_OUT = "sold_out"
    COMING_SOON = "coming_soon"
    CLOSED = "closed"


class TicketKind(StrEnum):
    FREE = "free"
    PAID = "paid"
    UNKNOWN = "unknown"


class EntryType(StrEnum):
    FREE = "free"
    PAID = "paid"
    FREE_AND_PAID = "free_and_paid"
    UNKNOWN = "unknown"


class TicketTier(BaseModel):
    source_ticket_id: str | None = None
    name: str
    description: str | None = None
    # Face value of the current round.
    price: Decimal | None = None
    original_price: Decimal | None = None
    # Online booking fee charged on top of ``price`` (None when unknown/included).
    service_fee: Decimal | None = None
    # Every price round of this tier in order (e.g. free until 23:30, then 80).
    price_rounds: list[float] = Field(default_factory=list)
    currency: str = "ILS"
    # Number of people admitted by one ticket (e.g. 2 for a couple ticket).
    admits: int = 1
    is_free: bool = False
    # Registration ticket whose price is paid at the venue (cash / box office).
    pay_at_door: bool = False
    is_sold_out: bool = False
    is_active: bool = True
    status: TierStatus = TierStatus.ON_SALE
    remaining: int | None = None
    sale_start: datetime | None = None
    sale_end: datetime | None = None

    @model_validator(mode="after")
    def _derive_status(self) -> TicketTier:
        """Keep the boolean flags and ``status`` consistent whichever one a scraper set."""
        if self.status is TierStatus.ON_SALE:
            if self.is_sold_out:
                self.status = TierStatus.SOLD_OUT
            elif not self.is_active:
                self.status = TierStatus.CLOSED
        self.is_sold_out = self.status is TierStatus.SOLD_OUT
        self.is_active = self.status is TierStatus.ON_SALE
        return self

    @computed_field
    @property
    def kind(self) -> TicketKind:
        if self.pay_at_door:
            return TicketKind.PAID
        if self.is_free or self.price == 0:
            return TicketKind.FREE
        return TicketKind.PAID if self.price is not None else TicketKind.UNKNOWN

    @computed_field
    @property
    def total_price(self) -> Decimal | None:
        """What the buyer pays: face value plus the online booking fee."""
        if self.price is None:
            return None
        return self.price + (self.service_fee or 0)


class ScrapedEvent(BaseModel):
    source: str
    source_event_id: str
    url: HttpUrl
    title: str
    description: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    venue_name: str | None = None
    address: str | None = None
    city: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    # Genres as reported by the source (normalized later by enrichment).
    source_genres: list[str] = Field(default_factory=list)
    event_types: list[str] = Field(default_factory=list)
    # Performers / DJs exactly as the source lists them.
    artists: list[str] = Field(default_factory=list)
    min_age: int | None = None
    organizer: str | None = None
    # Primary artwork plus every other image the source exposes (flyers, banners, gallery).
    image_url: str | None = None
    images: list[str] = Field(default_factory=list)
    currency: str = "ILS"
    min_price: Decimal | None = None
    max_price: Decimal | None = None
    is_sold_out: bool = False
    tickets: list[TicketTier] = Field(default_factory=list)
    # Source-specific parameters that have no dedicated column.
    extra: dict[str, Any] = Field(default_factory=dict)

    # Filled by enrichment.
    slug: str | None = None
    venue_slug: str | None = None
    genres: list[str] = Field(default_factory=list)
    lineup: list[str] = Field(default_factory=list)
    has_free_entry: bool = False
    entry_type: EntryType = EntryType.UNKNOWN
    in_target_city: bool | None = None
    local_date: date | None = None
    day_of_week: str | None = None
    time_slot: str | None = None
    is_weekend: bool | None = None

    @field_validator("start_at", "end_at")
    @classmethod
    def _require_tz(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("event datetimes must be timezone-aware")
        return value

    @model_validator(mode="after")
    def _merge_artwork(self) -> ScrapedEvent:
        """``images`` always starts with the primary artwork and holds no duplicates."""
        self.images = list(dict.fromkeys(i for i in [self.image_url, *self.images] if i))
        self.image_url = self.images[0] if self.images else None
        return self

    @property
    def event_id(self) -> str:
        return f"{self.source}:{self.source_event_id}"
