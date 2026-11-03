"""The events an email shows, for machines: schema.org Event JSON-LD in its <head> (Gmail's email markup)
and one .ics attachment with a VEVENT each (any calendar app). Both use the cards as render._cards left
them (``_url`` = our page with the email's UTM tags, ``_hero`` = our picture)."""

from __future__ import annotations

import base64
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from partylinks.bot.core.calendar import ics_many
from partylinks.config import Settings
from partylinks.db import EventRow

if TYPE_CHECKING:  # render imports this module
    from partylinks.mail.render import Section


def _cards(sections: list[Section]) -> list[dict[str, Any]]:
    return list({c["id"]: c for s in sections for c in s.events if c.get("start_at")}.values())


def jsonld(sections: list[Section]) -> list[dict[str, Any]]:
    out = []
    for c in _cards(sections):
        when = c["local_date"] if c.get("time_known") is False and c.get("local_date") else c["start_at"]
        item: dict[str, Any] = {
            "@context": "https://schema.org",
            "@type": "Event",
            "name": c["title"],
            "startDate": when,
            "url": c["_url"],
        }
        if c.get("end_at") and c.get("time_known") is not False:
            item["endDate"] = c["end_at"]
        if c.get("venue_name") or c.get("address") or c.get("city"):
            address = ", ".join(p for p in (c.get("address"), c.get("city")) if p)
            item["location"] = {
                "@type": "Place",
                "name": c.get("venue_name") or c.get("city"),
                "address": address or c.get("venue_name"),
            }
        if c.get("_hero"):
            item["image"] = c["_hero"]
        out.append(item)
    return out


def _at(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _row(c: dict[str, Any]) -> EventRow:
    """A transient row (never added to a session) for calendar._span / _where."""
    return EventRow(
        id=c["id"], title=c["title"], start_at=_at(c.get("start_at")), end_at=_at(c.get("end_at")),
        time_known=c.get("time_known") is not False, local_date=date.fromisoformat(c["local_date"]) if c.get("local_date") else None,
        venue_name=c.get("venue_name"), address=c.get("address"), city=c.get("city"),
    )  # fmt: skip


def attachment(settings: Settings, kind: str, sections: list[Section]) -> dict[str, str] | None:
    """Resend's attachment for this email's events, or None (no events, or switched off)."""
    cards = _cards(sections)
    if not (settings.mail.calendar_attachment and cards):
        return None
    site = settings.site.base_url.split("//")[-1].strip("/")
    body = ics_many([(_row(c), c["_url"]) for c in cards], site, by="mail")
    name = f"{settings.site.name.lower()}-{'week' if kind == 'digest' else 'events'}.ics"
    return {"filename": name, "content": base64.b64encode(body).decode(), "content_type": "text/calendar"}
