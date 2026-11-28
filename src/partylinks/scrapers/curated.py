"""Curated (SPOTX's own list): public events no site we can read lists, added by hand from the organiser's or a
news page that states them (config ``sources.curated.events``). Christmas 2026: Nazareth's municipal and church
programme (the Benedictus nights, the tree lighting, the market, the parade) is published only in articles, and
the Galilee villages' (Mi'ilya, Jish) only in guides. No request is made: each entry is read from the config,
and an event over is dropped as every source's (``is_current``). A date range is one event per day
(``daily``: shows and nightly fairs) or one event over its days (a market or festival of several days).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from pydantic import BaseModel, Field, HttpUrl

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings


class CuratedPrice(BaseModel):
    name: str
    price: Decimal


class CuratedEvent(BaseModel):
    id: str  # stable: the stored event's id
    title: str
    # "2026-12-02" or "2026-12-02..2026-12-06"
    dates: list[str]
    daily: bool = False  # one event per day of a range (else one event over the range)
    start: time | None = None  # unknown: the day only
    end: time | None = None
    venue: str
    address: str | None = None
    city: str
    latitude: float | None = None
    longitude: float | None = None
    description: str
    source_url: HttpUrl  # the page that states the event (its date): the event's link
    genres: list[str] = Field(default_factory=list)  # the event's kinds, as a source's categories
    free: bool = False
    prices: list[CuratedPrice] = Field(default_factory=list)


class CuratedSettings(SourceSettings):
    events: list[CuratedEvent] = Field(default_factory=list)
    free_label: str = "כניסה חופשית"  # the tier of a free entry


def _days(spec: str) -> list[date]:
    first, _, last = spec.partition("..")
    a, b = date.fromisoformat(first), date.fromisoformat(last or first)
    if b < a:
        raise ValueError(f"dates {spec!r}: the end is before the start")
    return [a + timedelta(days=n) for n in range((b - a).days + 1)]


def events_of(entry: CuratedEvent, timezone: str, free_label: str) -> list[ScrapedEvent]:
    """The entry's events: one per day (``daily``) or one per range."""
    tz = zone(timezone)
    spans = [[d] for spec in entry.dates for d in _days(spec)] if entry.daily else [_days(s) for s in entry.dates]
    tiers = [TicketTier(name=p.name, price=p.price, currency="ILS", is_free=p.price == 0) for p in entry.prices]
    if entry.free and not tiers:
        tiers = [TicketTier(name=free_label, price=Decimal(0), is_free=True)]
    prices = [t.price for t in tiers if t.price is not None]
    out = []
    for days in spans:
        first, last = days[0], days[-1]
        start = datetime.combine(first, entry.start or time(0), tz)
        if entry.end:
            end_day = last + timedelta(days=1) if entry.start and entry.end <= entry.start else last
            end: datetime | None = datetime.combine(end_day, entry.end, tz)
        else:  # a range with no hours ends with its last day
            end = datetime.combine(last + timedelta(days=1), time(0), tz) if len(days) > 1 else None
        suffix = f":{first.isoformat()}" if len(spans) > 1 else ""
        out.append(
            ScrapedEvent(
                source="curated",
                source_event_id=entry.id + suffix,
                url=f"{entry.source_url}#{entry.id}{suffix}",
                title=entry.title,
                description=entry.description,
                start_at=start,
                end_at=end,
                time_known=entry.start is not None,
                venue_name=entry.venue,
                address=entry.address,
                city=entry.city,
                latitude=entry.latitude,
                longitude=entry.longitude,
                source_genres=entry.genres,
                tickets=tiers,
                min_price=min(prices) if prices else None,
                max_price=max(prices) if prices else None,
                extra={"purchase_url": str(entry.source_url)},
            )
        )
    return out


class CuratedScraper(BaseScraper[CuratedSettings]):
    name = "curated"
    settings_model = CuratedSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        retention = RetentionSettings()
        for entry in self.settings.events:
            for event in events_of(entry, self.timezone, self.settings.free_label):
                if is_current(event.start_at, event.end_at, retention, self.timezone, time_known=event.time_known):
                    yield event
