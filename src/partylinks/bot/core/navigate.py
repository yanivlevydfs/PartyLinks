"""➕ 📍 under a card: the venue as a Telegram map pin with Waze and Google Maps buttons, and for a place
(a bar, a restaurant) what the site's place page shows: hours, phone, Ontopo booking, website."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from partylinks.bot.core import refs, usage
from partylinks.bot.core.phrases import WEEKDAY_NAMES, say
from partylinks.bot.core.reply import Button, Reply
from partylinks.db import EventRow
from partylinks.placeinfo.details import place_details
from partylinks.placeinfo.models import PlaceInfoRow
from partylinks.placeinfo.pages import phone_key
from partylinks.venues import PLACE_LISTING

if TYPE_CHECKING:
    from partylinks.bot.core.conversation import Conversation
    from partylinks.config import Settings

NAVIGATE = "nav:"
SUNDAY_FIRST = (6, 0, 1, 2, 3, 4, 5)  # WEEKDAY_NAMES are Monday first; the site's week starts on Sunday

TEXT: dict[str, dict[str, str]] = {
    "button": {"he": "📍 איך מגיעים", "en": "📍 How to get there"},
    "button_place": {"he": "📍 ניווט ופרטי המקום", "en": "📍 Directions and info"},
    "here": {"he": "📍 ״{t}״", "en": "📍 “{t}”"},
    "no_pin": {"he": "אין לי מיקום מדויק ל״{t}״ 🤷", "en": "I don't have an exact location for “{t}” 🤷"},
    "hours": {"he": "🕒 שעות פתיחה:", "en": "🕒 Opening hours:"},
    "closed": {"he": "סגור", "en": "Closed"},
    "phone": {"he": "📞 {p}", "en": "📞 {p}"},
    "waze": {"he": "🚗 Waze", "en": "🚗 Waze"},
    "maps": {"he": "🗺 Google Maps", "en": "🗺 Google Maps"},
    "ontopo": {"he": "📅 הזמנת מקום ב-Ontopo", "en": "📅 Book on Ontopo"},
    "book": {"he": "📅 הזמנת מקום", "en": "📅 Book a table"},
    "website": {"he": "🌐 אתר", "en": "🌐 Website"},
}


def _t(key: str, lang: str, **values: object) -> str:
    return TEXT[key][lang].format(**values)


def button(row: EventRow, ref: str | int, lang: str) -> Button:
    """The ➕ menu's row for this event."""
    return Button(_t("button_place" if (row.extra or {}).get(PLACE_LISTING) else "button", lang), f"{NAVIGATE}{ref}")


def _place(session: Session, row: EventRow, lang: str, cfg: Settings) -> dict[str, Any]:
    """place_details for a place or an Ontopo match; nothing for an ordinary event."""
    if not ((row.extra or {}).get(PLACE_LISTING) or session.get(PlaceInfoRow, row.url)):
        return {}
    return place_details(session, row, cfg.place_info, lang)


def _week(hours: dict[str, list[str]], lang: str) -> list[str]:
    days = [(WEEKDAY_NAMES[lang][i], WEEKDAY_NAMES["en"][i]) for i in SUNDAY_FIRST]
    return [_t("hours", lang)] + [f"{name}: {', '.join(hours.get(day) or [_t('closed', lang)])}" for name, day in days]


def _phone(phone: str) -> str:
    """+972 so Telegram makes it a tap-to-call link."""
    digits = phone_key(phone)
    return f"+972{digits[1:]}" if digits and digits.startswith("0") else phone


async def press(conv: Conversation, chat_id: str, lang: str, ref: str) -> Reply:
    cfg = conv.settings()

    def work(session: Session) -> tuple[str, EventRow | None, dict[str, Any]]:
        chat = conv._chat(session, chat_id, lang)
        row = refs.event(session, ref)
        if row is None:
            return chat.lang, None, {}
        place = _place(session, row, chat.lang, cfg)
        usage.record(session, conv.channel, chat_id, "navigate")
        if place:
            usage.record(session, conv.channel, chat_id, "place_info")
        return chat.lang, row, place

    lang, row, place = await conv._in_db(work)
    if row is None:
        return Reply(say("gone", lang))
    title = row.venue_name or row.title
    address = place.get("address") or row.address or row.city
    pin = row.latitude is not None and row.longitude is not None
    lines = [_t("here" if pin else "no_pin", lang, t=title), *([address] if address else [])]
    if place.get("hours"):
        lines += ["", *_week(place["hours"], lang)]
    if place.get("phone"):
        lines += ["", _t("phone", lang, p=_phone(place["phone"]))]
    buttons: list[list[Button]] = []
    if pin:
        ll = f"{row.latitude},{row.longitude}"
        buttons.append([
            Button(_t("waze", lang), url=f"https://waze.com/ul?ll={ll}&navigate=yes"),
            Button(_t("maps", lang), url=f"https://www.google.com/maps/dir/?api=1&destination={ll}"),
        ])  # fmt: skip
    if book := place.get("ontopo_url") or place.get("booking_url"):
        buttons.append([Button(_t("ontopo" if place.get("ontopo_url") else "book", lang), url=book)])
    if place.get("website"):
        buttons.append([Button(_t("website", lang), url=place["website"])])
    venue = (row.latitude, row.longitude, title, address or title) if pin else None
    return Reply("\n".join(lines), buttons=buttons, venue=venue)
