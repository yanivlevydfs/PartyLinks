"""The forecast line of a card, from the site's own weather cache (partylinks.weather, MET Norway):
the same slot and words as web/src/Weather.tsx. Nothing for events too far ahead or with no place."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from partylinks import weather
from partylinks.config import Settings
from partylinks.db import EventRow

log = logging.getLogger(__name__)
HORIZON = timedelta(days=10)  # MET's forecast reaches about this far
UNDER_WAY = timedelta(hours=6)  # an event that started this recently takes the first slot

WORDS = {
    "he": {"clear": "בהיר", "partly": "מעונן חלקית", "cloudy": "מעונן", "fog": "ערפל", "drizzle": "טפטוף",
           "rain": "גשם", "showers": "ממטרים", "thunder": "סופות רעמים", "snow": "שלג", "sleet": "שלג וגשם"},
    "en": {"clear": "Clear", "partly": "Partly cloudy", "cloudy": "Cloudy", "fog": "Fog", "drizzle": "Drizzle",
           "rain": "Rain", "showers": "Showers", "thunder": "Thunderstorms", "snow": "Snow", "sleet": "Sleet"},
}  # fmt: skip
ICONS = {"clear": ("☀️", "🌙"), "partly": ("⛅", "☁️"), "cloudy": ("☁️", "☁️"), "fog": ("🌫️", "🌫️"),
         "drizzle": ("🌦️", "🌧️"), "rain": ("🌧️", "🌧️"), "showers": ("🌦️", "🌧️"), "thunder": ("⛈️", "⛈️"),
         "snow": ("🌨️", "🌨️"), "sleet": ("🌨️", "🌨️")}  # fmt: skip


def word_for(symbol: str) -> str:
    """MET symbol ("rainshowers_day") -> a key of WORDS (Weather.tsx iconFor)."""
    base = symbol.split("_")[0]
    if "thunder" in base:
        return "thunder"
    if "sleet" in base:
        return "sleet"
    if "snow" in base:
        return "snow"
    if base in ("lightrain", "lightrainshowers"):
        return "drizzle"
    if "rain" in base:
        return "showers" if "showers" in base else "rain"
    return {"fog": "fog", "cloudy": "cloudy", "clearsky": "clear"}.get(base, "partly")


def slot_at(slots: list[list], start: datetime, now: datetime) -> list | None:
    """The slot whose window holds ``start`` (an event already under way takes the first slot)."""
    if slots and start < datetime.fromisoformat(slots[0][0]) and start > now - UNDER_WAY:
        return slots[0]
    for slot in slots:
        begin = datetime.fromisoformat(slot[0])
        if begin <= start < begin + timedelta(hours=slot[4]):
            return slot
    return None


def forecast_at(row: EventRow) -> datetime | None:
    """Its start; a date alone (no time from the seller), that day's noon UTC (Weather.tsx forecastAt)."""
    if not row.time_known and row.local_date:
        return datetime.combine(row.local_date, time(12), tzinfo=UTC)
    return row.start_at


async def line(settings: Settings, row: EventRow, lang: str) -> str | None:
    """"⛅ 21° · מעונן חלקית", or None (too far ahead, no place, MET down). Never raises: a card
    without weather, never no card."""
    start, now = forecast_at(row), datetime.now(UTC)
    if start is None or not (now - UNDER_WAY < start < now + HORIZON):
        return None
    try:
        point = (row.latitude, row.longitude) if row.latitude is not None and row.longitude is not None else None
        if point is None and row.city:  # placed only by its city: the city centre (the first call builds an index)
            point = await asyncio.to_thread(weather._city_center, settings, row.city)
        if point is None:
            return None
        slots = await weather.forecast(
            point[0], point[1], f"SPOTX/1.0 (+{settings.site.base_url})", weather.cache_file(settings), settings.weather
        )
        slot = slot_at(slots, start, now)
        if slot is None:
            return None
        _, symbol, temp, rain, _ = slot
        key = word_for(symbol)
        hour = start.astimezone(ZoneInfo(row.time_zone or settings.timezone)).hour
    except Exception:
        log.exception("bot: forecast for %s failed", row.id)
        return None
    icon = ICONS[key][1 if (hour < 6 or hour >= 18) and key in ("clear", "partly") else 0]
    text = f"{icon} {round(temp)}° · {WORDS[lang][key]}" if temp is not None else f"{icon} {WORDS[lang][key]}"
    return text + (f" · {rain} {'מ״מ' if lang == 'he' else 'mm'}" if rain else "")
