"""Emails as HTML (Jinja2, table layout, inline styles: what mail clients draw) and plain text, in Hebrew
(right to left) or English. Every event card has its picture, day and time, place, weather and price."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from partylinks import weather
from partylinks.config import Settings
from partylinks.utm import tag_url

log = logging.getLogger(__name__)
_env = Environment(
    loader=FileSystemLoader(Path(__file__).with_name("templates")), autoescape=select_autoescape(["html"])
)

DAYS = {
    "he": ["יום ב׳", "יום ג׳", "יום ד׳", "יום ה׳", "יום ו׳", "שבת", "יום א׳"],
    "en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
}
SKY = (  # MET Norway symbol codes, by prefix
    ("clearsky", "☀️"), ("fair", "🌤️"), ("partlycloudy", "⛅"), ("cloudy", "☁️"), ("fog", "🌫️"),
    ("rainshowersandthunder", "⛈️"), ("rainandthunder", "⛈️"), ("heavyrainandthunder", "⛈️"),
    ("rainshowers", "🌦️"), ("lightrainshowers", "🌦️"), ("heavyrainshowers", "🌦️"),
    ("rain", "🌧️"), ("lightrain", "🌧️"), ("heavyrain", "🌧️"), ("sleet", "🌨️"), ("snow", "🌨️"),
)  # fmt: skip

TEXT: dict[str, dict[str, Any]] = {
    "he": {
        "dir": "rtl", "free": "כניסה חופשית", "from": "החל מ-", "soldOut": "אזל", "open": "לפרטים ולכרטיסים", "tickets": "לכרטיסים",
        "why": "קיבלת את המייל כי ביקשת אותו בחשבון שלך ב-SPOTX.",
        "stop": "להפסקת המיילים האלה", "settings": "הגדרות החשבון", "contact": "יצירת קשר",
        "welcome_subject": "ברוכים הבאים ל-SPOTX 🎉",
        "welcome_title": "ברוכים הבאים, {name}!",
        "welcome_body": ["החשבון שלך מוכן. ככה מוציאים ממנו את המקסימום:"],
        "welcome_steps": [
            ("🔔", "שומרים אירועים", "לוחצים על הפעמון בכל אירוע, והוא מחכה לכם ב\"שמורים\" בכל מכשיר."),
            ("✨", "מקבלים התראות", "בוחרים סינון (ז׳אנר, אזור, מתי) ולוחצים \"שמירה כהתראה\": כל אירוע חדש שמתאים יופיע אצלכם."),
            ("📬", "עדכון שבועי במייל", "בחשבון אפשר להפעיל מייל קצר כל חמישי בבוקר, עם מה ששמרתם ומה חדש לסופ״ש."),
        ],
        "welcome_kicker": "ברוכים הבאים", "welcome_preheader": "שמירת אירועים, התראות על מסיבות ועדכון שבועי",
        "digest_kicker": "העדכון השבועי", "digest_label": "העדכון השבועי · {date}",
        "instant_kicker": "התראה חדשה", "instant_label": "התראה · {date}",
        "welcome_button": "לאירועים", "welcome_why": "קיבלת את המייל כי נרשמת ל-SPOTX עם חשבון Google.",
        "instant_subject": "🔔 {n} אירועים חדשים שמתאימים לך",
        "instant_subject_1": "🔔 אירוע חדש שמתאים לך: {title}",
        "instant_title": "עלו אירועים חדשים שמתאימים להתראות שלך",
        "digest_subject": "🎉 מה קורה השבוע: {n} אירועים בשבילך",
        "digest_title": "העדכון השבועי שלך",
        "digest_saved": "🔔 השמורים שלך השבוע",
        "digest_matches": "✨ חדש בהתראות שלך",
        "digest_free": "🎟️ כניסה חופשית בסופ״ש",
        "digest_weekend": "🔥 בסופ״ש הקרוב",
        "alerts_button": "להתראות שלי",
    },
    "en": {
        "dir": "ltr", "free": "Free entry", "from": "From ", "soldOut": "Sold out", "open": "Details and tickets", "tickets": "Tickets",
        "why": "You're getting this email because you asked for it in your SPOTX account.",
        "stop": "Stop these emails", "settings": "Account settings", "contact": "Contact",
        "welcome_subject": "Welcome to SPOTX 🎉",
        "welcome_title": "Welcome, {name}!",
        "welcome_body": ["Your account is ready. Here's how to get the most out of it:"],
        "welcome_steps": [
            ("🔔", "Save events", "Tap the bell on any event and it waits for you under \"Saved\" on every device."),
            ("✨", "Get alerts", "Pick filters (genre, area, when) and press \"Save as alert\": every new event that matches shows up for you."),
            ("📬", "A weekly email", "Turn on a short email every Thursday morning in your account: what you saved and what's new this weekend."),
        ],
        "welcome_kicker": "Welcome", "welcome_preheader": "Save events, get party alerts and a weekly update",
        "digest_kicker": "Weekly update", "digest_label": "Weekly update · {date}",
        "instant_kicker": "New alert", "instant_label": "Alert · {date}",
        "welcome_button": "See events", "welcome_why": "You're getting this email because you signed up to SPOTX with Google.",
        "instant_subject": "🔔 {n} new events that suit you",
        "instant_subject_1": "🔔 A new event that suits you: {title}",
        "instant_title": "New events match your alerts",
        "digest_subject": "🎉 What's on this week: {n} events for you",
        "digest_title": "Your weekly update",
        "digest_saved": "🔔 Your saved events this week",
        "digest_matches": "✨ New for your alerts",
        "digest_free": "🎟️ Free entry this weekend",
        "digest_weekend": "🔥 This weekend",
        "alerts_button": "My alerts",
    },
}  # fmt: skip


@dataclass
class Section:
    title: str
    events: list[dict[str, Any]]


def _sky(symbol: str) -> str:
    base = symbol.split("_")[0]
    return next((icon for prefix, icon in SKY if base == prefix), "🌡️")


def _forecasts(settings: Settings, cards: list[dict[str, Any]]) -> dict[str, str]:
    """Event id -> "☀️ 24°" at its start, from the site's weather cache (fetches only what is missing)."""
    spots: dict[str, tuple[float, float]] = {}
    for c in cards:
        if c.get("latitude") is not None and c.get("longitude") is not None:
            spots[c["id"]] = (c["latitude"], c["longitude"])
        elif c.get("city") and (center := weather._city_center(settings, c["city"])):
            spots[c["id"]] = center
    cells = {(round(lat, 1), round(lng, 1)) for lat, lng in spots.values()}
    agent = f"SPOTX/1.0 (+{settings.site.base_url})"
    path = weather.cache_file(settings)

    async def fetch() -> dict[tuple[float, float], list[list[Any]]]:
        got = await asyncio.gather(
            *(weather.forecast(lat, lng, agent, path) for lat, lng in cells), return_exceptions=True
        )
        return {cell: g for cell, g in zip(cells, got, strict=True) if isinstance(g, list)}

    try:
        slots = asyncio.run(fetch()) if cells else {}
    except Exception:  # weather is a nice-to-have: an email goes without it
        log.exception("mail: weather failed")
        return {}
    out: dict[str, str] = {}
    for c in cards:
        if c["id"] not in spots or not c.get("start_at"):
            continue
        lat, lng = spots[c["id"]]
        start = datetime.fromisoformat(c["start_at"])
        cell = slots.get((round(lat, 1), round(lng, 1)), [])
        fitting = [s for s in cell if datetime.fromisoformat(s[0]) <= start]
        # Started already (today's morning tour): the forecast begins now, so its first slot.
        s = fitting[-1] if fitting else (cell[0] if cell else None)
        if s is not None and start > datetime.fromisoformat(s[0]) + timedelta(hours=s[4]):
            s = None  # past the forecast's reach: no weather rather than the wrong day's
        if s is not None and s[2] is not None:
            out[c["id"]] = f"{_sky(s[1])} {round(s[2])}°"
    return out


def _when(card: dict[str, Any], lang: str, tz: str) -> str:
    if not card.get("start_at"):
        return ""
    start = datetime.fromisoformat(card["start_at"]).astimezone(ZoneInfo(card.get("time_zone") or tz))
    day = f"{DAYS[lang][start.weekday()]} {start.day}.{start.month}"
    return day if card.get("time_known") is False else f"{day} · {start:%H:%M}"


def _price(card: dict[str, Any], t: dict[str, Any]) -> str:
    if card.get("is_sold_out"):
        return t["soldOut"]
    low = card.get("group_min_price") if card.get("group_min_price") is not None else card.get("min_price")
    free = card.get("group_has_free_entry") or card.get("has_free_entry")
    if card.get("entry_type") == "free" or low == 0 or (low is None and free):
        return t["free"]
    if low is None:
        return ""
    sign = {"ILS": "₪", "EUR": "€", "USD": "$", "GBP": "£"}.get(card.get("currency") or "ILS", "")
    return f"{t['from']}{sign}{low:g}"


def link(settings: Settings, path: str, kind: str, lang: str) -> str:
    """A link to our site, tagged so the visit counts as coming from this email."""
    params = {"utm_source": "email", "utm_medium": "email", "utm_campaign": kind}
    if lang == "en":
        params["lang"] = "en"
    return tag_url(settings.site.base_url.rstrip("/") + path, params)


def _cards(settings: Settings, sections: list[Section], kind: str, lang: str) -> None:
    """Adds what the template draws to each card, in place."""
    t = TEXT[lang]
    everything = [c for s in sections for c in s.events]
    sky = _forecasts(settings, everything)
    base = settings.site.base_url.rstrip("/")
    for c in everything:
        image = c.get("cover_url") or c.get("image_url")
        if image and image.startswith("/images/"):
            image = f"{base}{image}/640.webp"
        c["_url"] = link(settings, f"/event/{c.get('slug') or c['id']}", kind, lang)
        c["_image"] = image if image and image.startswith(("https://", "http://")) else None
        c["_when"] = _when(c, lang, settings.timezone)
        c["_place"] = " · ".join(x for x in (c.get("venue_name"), c.get("city")) if x)
        c["_weather"] = sky.get(c["id"], "")
        c["_price"] = _price(c, t)


def _today(settings: Settings) -> str:
    now = datetime.now(ZoneInfo(settings.timezone))
    return f"{now.day}.{now.month}"


def _preheader(sections: list[Section]) -> str:
    """The inbox's line under the subject: the first events' names."""
    names = [e["title"] for s in sections for e in s.events][:3]
    return " · ".join(names)


def render(
    settings: Settings, *, kind: str, lang: str, name: str, sections: list[Section], unsubscribe_url: str,
    title: str, subject: str, intro: list[str] | None = None, button: tuple[str, str] | None = None, why: str | None = None,
    steps: list[tuple[str, str, str]] | None = None,
) -> tuple[str, str, str]:  # fmt: skip
    """(subject, html, text) of one email."""
    lang = lang if lang in TEXT else "he"
    t = TEXT[lang]
    _cards(settings, sections, kind, lang)
    context = {
        "t": t, "lang": lang, "title": title, "intro": intro or [], "sections": sections, "button": button,
        "why": why or t["why"], "unsubscribe": unsubscribe_url, "name": name,
        "settings_url": link(settings, "/account", kind, lang), "contact_url": link(settings, "/contact", kind, lang),
        "home": link(settings, "/", kind, lang), "site": settings.site.name,
        "logo": settings.site.base_url.rstrip("/") + "/icon-192.png", "steps": steps or [],
        "kicker": t.get(f"{kind}_kicker", ""), "label": t.get(f"{kind}_label", "").format(date=_today(settings)),
        "preheader": t.get(f"{kind}_preheader") or _preheader(sections),
    }  # fmt: skip
    return subject, _env.get_template("email.html").render(context), _env.get_template("email.txt").render(context)
