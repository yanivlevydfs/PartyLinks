"""What the /submit form sends. ``EventIn`` is the event as stored in ``community_events.data``; its fixed bounds
are checked by pydantic, the admin-set ones (lengths, counts, the category lists, how far ahead) by ``problems``.
Times are the venue's local wall clock (Israel's unless ``time_zone`` names another zone)."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

from partylinks.community.config import CommunitySettings
from partylinks.community.links import BadLinkError, checked_link

Line = Annotated[str, StringConstraints(strip_whitespace=True, max_length=160)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
Price = Annotated[Decimal, Field(ge=0, le=100_000, max_digits=8, decimal_places=2)]
CURRENCIES = ("ILS", "USD", "EUR", "GBP")


class TicketIn(BaseModel):
    """One ticket link and its tier: what the buyer sees beside the button."""

    name: Name  # "Early bird", "Couple", "At the door"
    url: Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=1000)]
    price: Price | None = None  # None: not stated
    original_price: Price | None = None  # before a discount
    service_fee: Price | None = None  # booking fee on top of price
    currency: Literal["ILS", "USD", "EUR", "GBP"] = "ILS"
    admits: int = Field(1, ge=1, le=20)  # people one ticket lets in (2: a couple ticket)
    is_free: bool = False  # a free registration
    pay_at_door: bool = False  # registered here, paid at the venue
    sold_out: bool = False
    sale_end: datetime | None = None  # local wall clock, like the event's times

    @model_validator(mode="after")
    def _prices(self) -> TicketIn:
        if self.is_free:
            self.price, self.original_price, self.service_fee = Decimal(0), None, None
        if self.original_price is not None and (self.price is None or self.original_price <= self.price):
            self.original_price = None  # no discount to show
        return self


class EventIn(BaseModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=300)]
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=20000)] = ""
    organizer: Line = ""
    artists: list[Name] = Field(default_factory=list, max_length=60)
    min_age: int | None = Field(None, ge=0, le=99)
    # When: the start day, its hour (None: the hour is not known yet), the end, the doors.
    start_date: date
    start_time: time | None = None
    end_date: date | None = None  # None: the start day (or the next one when the end hour is before the start)
    end_time: time | None = None
    doors_open: time | None = None
    # Where: a venue (picked from ours, with its coordinates, or typed), its address and town.
    venue_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)]
    address: Line = ""
    city: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=60)]
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    country: Annotated[str, StringConstraints(to_upper=True, pattern=r"^[A-Z]{2}$")] | None = None  # None: Israel
    time_zone: Annotated[str, StringConstraints(max_length=48)] | None = None  # IANA, abroad only
    # Categories: kinds of event (CommunitySettings.event_types) and music / show genres (enrichment.genres keys).
    event_types: list[Annotated[str, StringConstraints(max_length=40)]] = Field(default_factory=list, max_length=10)
    genres: list[Annotated[str, StringConstraints(max_length=40)]] = Field(default_factory=list, max_length=20)
    # Entry: ticket links, and / or free entry with no ticket at all (the site sells no tickets).
    tickets: list[TicketIn] = Field(default_factory=list, max_length=30)
    free_entry: bool = False
    pictures: list[Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]] = Field(
        default_factory=list, max_length=10
    )
    # "I want a sponsored link": a promotion request (promote_requests) is opened for this event.
    wants_sponsored: bool = False
    sponsor_phone: Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)] = ""
    sponsor_note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=5000)] = ""

    @field_validator("artists", "event_types", "genres", "pictures")
    @classmethod
    def _unique(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(v for v in values if v))

    @model_validator(mode="after")
    def _shape(self) -> EventIn:
        if not self.tickets and not self.free_entry:
            raise ValueError("tickets: add a ticket link, or mark the entry free with no ticket")
        if self.end_time is None and self.end_date is not None:
            raise ValueError("end_time: an end day needs an end hour")
        if self.start_time is None and (self.end_time is not None or self.doors_open is not None):
            raise ValueError("start_time: needed when an end hour or a doors hour is given")
        if self.country == "IL":
            self.country = None
        if self.country is None:
            self.time_zone = None
        if self.country and not self.time_zone:
            raise ValueError("time_zone: an event abroad needs its time zone")
        if self.wants_sponsored and len(self.sponsor_phone) < 7:
            raise ValueError("sponsor_phone: a sponsored link needs a mobile number to call")
        return self


class SubmitIn(EventIn):
    """The form as sent: the event, the CAPTCHA's answer, the poster's agreement to the terms and the hidden field."""

    captcha_token: Annotated[str, StringConstraints(max_length=4096)] = ""
    # "I agree to the Terms and the Privacy Policy; the event is mine to answer for, SPOTX bears no responsibility
    # for it" (web/src/community, the terms' user-events section). Saved with the event as terms_accepted_at.
    accept_terms: Literal[True]
    website: str = ""  # hidden from people: a bot that fills it gets a quiet "thanks" and nothing is saved

    def event(self) -> EventIn:
        return EventIn.model_validate(self.model_dump(exclude={"captcha_token", "accept_terms", "website"}))


def zone(event: EventIn, default: str) -> ZoneInfo:
    try:
        return ZoneInfo(event.time_zone or default)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"time_zone: unknown zone {event.time_zone}") from exc


def start_at(event: EventIn, tz: ZoneInfo) -> datetime:
    """The start, timezone-aware: the day's midnight when the hour is not known (ScrapedEvent.time_known)."""
    return datetime.combine(event.start_date, event.start_time or time(0), tz)


def end_at(event: EventIn, tz: ZoneInfo) -> datetime | None:
    if event.end_time is None:
        return None
    end = datetime.combine(event.end_date or event.start_date, event.end_time, tz)
    start = start_at(event, tz)
    if event.end_date is None and end <= start:
        end += timedelta(days=1)  # "22:00 - 04:00": the night ends the next morning
    return end


def problems(event: EventIn, cfg: CommunitySettings, genres: set[str], default_zone: str,
             now: datetime | None = None) -> list[str]:  # fmt: skip
    """Every way the event breaks the admin-set limits ([] when none). Links are normalized in place."""
    found: list[str] = []
    if len(event.title) > cfg.title_max:
        found.append(f"title: at most {cfg.title_max} characters")
    if not cfg.description_min <= len(event.description) <= cfg.description_max:
        found.append(f"description: {cfg.description_min}-{cfg.description_max} characters")
    for name, values, most in (("artists", event.artists, cfg.max_artists), ("genres", event.genres, cfg.max_genres),
                               ("event_types", event.event_types, cfg.max_event_types),
                               ("tickets", event.tickets, cfg.max_tickets), ("pictures", event.pictures, cfg.max_images)):  # fmt: skip
        if len(values) > most:
            found.append(f"{name}: at most {most}")
    if unknown := [t for t in event.event_types if t not in cfg.event_types]:
        found.append(f"event_types: unknown {', '.join(unknown)}")
    if unknown := [g for g in event.genres if g not in genres]:
        found.append(f"genres: unknown {', '.join(unknown)}")
    if len(event.sponsor_note) > cfg.sponsor_note_max:
        found.append(f"sponsor_note: at most {cfg.sponsor_note_max} characters")
    for i, ticket in enumerate(event.tickets):
        try:
            ticket.url = checked_link(ticket.url, cfg.blocked_link_hosts)
        except BadLinkError as exc:
            found.append(f"tickets[{i}].{exc}")
    try:
        tz = zone(event, default_zone)
    except ValueError as exc:
        return [*found, str(exc)]
    now = now or datetime.now(UTC)
    start, end = start_at(event, tz), end_at(event, tz)
    # Over once it ended; with no end, once it started (with no hour either, once its day is over).
    last = end or (start + timedelta(days=1) if event.start_time is None else start)
    if last < now:
        found.append("start_date: the event is over")
    if start > now + timedelta(days=cfg.max_days_ahead):
        found.append(f"start_date: at most {cfg.max_days_ahead} days ahead")
    if end is not None and end > start + timedelta(days=30):
        found.append("end_date: at most 30 days after the start")
    return found
