"""An event -> a chat card: name, when, where, weather, price, a picture, ticket and details links.
Ticket links go through /go/ (counted, UTM-tagged, ``at=tg``) like every ticket link the site hands out."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from partylinks import seo
from partylinks.bot.core import forecast
from partylinks.bot.core.find import distance_km
from partylinks.bot.core.phrases import label
from partylinks.bot.core.reply import OPTIONS, REMIND, SIMILAR, WATCH, Button, Card
from partylinks.config import Settings
from partylinks.db import EventRow
from partylinks.mail.images import HERO, mail_image

PLACEMENT = "tg"  # utm.PLACEMENTS: where the click came from
WEEKDAYS = {
    "he": ("שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"),
    "en": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
}
EN_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
CURRENCY = {"ILS": "₪", "USD": "$", "EUR": "€", "GBP": "£"}


def when(row: EventRow, cfg: Settings, lang: str, now: datetime | None = None, start: datetime | None = None) -> str:
    """"🗓 הערב · 23:00", "🗓 שישי 7.11 · 22:00", "🗓 Fri 7 Nov": in the venue's own clock. ``start``: another
    time than the listing's (a place's opening hours, evening.plan)."""
    if row.start_at is None:
        return ""
    zone = ZoneInfo(row.time_zone or cfg.timezone)
    day = start.astimezone(zone).date() if start else row.local_date
    start = (start or row.start_at).astimezone(zone)
    day = day or start.date()
    today = (now or datetime.now(UTC)).astimezone(zone).date()
    if day == today:
        name = label("tonight_at" if start.hour >= cfg.bot.evening_from_hour or not row.time_known else "today_at", lang)
    elif day == today + timedelta(days=1):
        name = label("tomorrow_at", lang)
    else:
        weekday = WEEKDAYS[lang][day.weekday()]
        name = f"{weekday} {day.day}.{day.month}" if lang == "he" else f"{weekday} {day.day} {EN_MONTHS[day.month - 1]}"
    return f"🗓 {name}" + (f" · {start:%H:%M}" if row.time_known else "")


def where(row: EventRow) -> str:
    parts = [p for p in (row.venue_name, row.city) if p]
    if len(parts) == 2 and parts[1].casefold() in parts[0].casefold():
        parts = parts[:1]  # "Barby Tel Aviv, Tel Aviv"
    return "📍 " + ", ".join(parts) if parts else ""


def money(amount: Decimal | float, currency: str) -> str:
    value = f"{float(amount):,.0f}" if float(amount) == int(amount) else f"{float(amount):,.2f}"
    sign = CURRENCY.get(currency)
    return f"{value} {sign}" if sign else f"{value} {currency}"


def price(row: EventRow, lang: str, extra: dict[str, Any]) -> str:
    deal = extra.get("best_deal")
    if row.is_sold_out and not (deal and deal.id != row.id):  # sold out here, but not at another seller
        return label("sold_out", lang)
    if row.group_entry_type == "free_and_paid":  # a 0 RSVP tier beside paid tickets: not a free event
        return label("free_and_paid", lang)
    if row.group_has_free_entry:
        return label("free_entry", lang)
    text = label("from_price", lang, p=money(row.group_min_price, row.currency)) if row.group_min_price else ""
    if row.has_discount:
        text = f"{text} · {label('discount', lang)}" if text else label("discount", lang)
    return text


def links(row: EventRow, extra: dict[str, Any], cfg: Settings, lang: str) -> tuple[Button, ...]:
    base = cfg.site.base_url.rstrip("/")
    deal = extra.get("best_deal")
    go = (deal.url if deal else seo.go_path(row.id)) + f"?at={PLACEMENT}"
    page = {"utm_source": "telegram", "utm_medium": "bot"} | ({"lang": "en"} if lang == "en" else {})
    return (
        Button(label("tickets", lang), url=base + go),
        Button(label("details", lang), url=f"{base}{seo.event_path(row)}?{urlencode(page)}"),
    )


def photo(extra: dict[str, Any], cfg: Settings) -> str | None:
    """A JPEG of the event's picture (or its drawn card): chat apps fetch JPEG reliably, WebP not always."""
    cover = extra.get("cover_url")
    return cfg.site.base_url.rstrip("/") + mail_image(cover, HERO) if cover else None


def actions(extra: dict[str, Any], lang: str) -> tuple[Button, ...]:
    """⏰ remind, 👁 watch, 🔁 similar, ➕ more: by the event's short number (refs.py); none without one."""
    ref = extra.get("ref")
    if ref is None:
        return ()
    return tuple(Button(label(key, lang), f"{prefix}{ref}") for key, prefix in
                 (("remind", REMIND), ("watch", WATCH), ("similar", SIMILAR), ("options", OPTIONS)))  # fmt: skip


async def card(
    row: EventRow, extra: dict[str, Any], cfg: Settings, lang: str, near: tuple[float, float] | None = None,
    with_weather: bool = True,
) -> Card:  # fmt: skip
    lines = [when(row, cfg, lang), where(row)]
    if near and row.latitude is not None and row.longitude is not None:
        km = distance_km(near[0], near[1], row.latitude, row.longitude)
        lines.append(label("km", lang, km=f"{km:.1f}"))
    lines += [(await forecast.line(cfg, row, lang) if with_weather else None) or "", price(row, lang, extra)]
    if extra.get("in_text"):
        lines.append(label("in_text", lang))
    return Card(row.id, row.title, tuple(x for x in lines if x), photo(extra, cfg), links(row, extra, cfg, lang), actions(extra, lang))


async def cards(
    rows: list[EventRow], extras: dict[str, dict[str, Any]], cfg: Settings, lang: str,
    near: tuple[float, float] | None = None, with_weather: bool = True,
) -> list[Card]:  # fmt: skip
    """Every row's card; their forecasts are fetched together."""
    return list(await asyncio.gather(*(card(r, extras.get(r.id, {}), cfg, lang, near, with_weather) for r in rows)))
