"""A place's page as crawlers see it: schema.org LocalBusiness, not Event.

Google forbids marking business hours as an Event, and "don't aggregate reviews or ratings from other
websites": the rating stays on the page, out of the markup.
developers.google.com/search/docs/appearance/structured-data/{event,local-business,review-snippet}
"""

from __future__ import annotations

from html import escape
from typing import Any

from sqlalchemy.orm import Session

from partylinks.db import EventRow
from partylinks.placeinfo.config import PlaceInfoSettings
from partylinks.placeinfo.details import place_details

_DAY_NAMES = {
    "he": {"Sunday": "ראשון", "Monday": "שני", "Tuesday": "שלישי", "Wednesday": "רביעי", "Thursday": "חמישי", "Friday": "שישי", "Saturday": "שבת"},
    "en": {},
}  # fmt: skip
_TEXT = {
    "he": {"hours": "שעות פתיחה", "phone": "טלפון", "book": "הזמנת מקום ב-Ontopo", "rating": "בגוגל", "reviews": "ביקורות"},
    "en": {"hours": "Opening hours", "phone": "Phone", "book": "Book on Ontopo", "rating": "on Google", "reviews": "reviews"},
}  # fmt: skip


def _type(genres: list[str] | None, cfg: PlaceInfoSettings) -> str:
    return next((cfg.schema_types[g] for g in genres or [] if g in cfg.schema_types), "LocalBusiness")


def _hours_specs(hours: dict[str, list[str]]) -> list[dict[str, str]]:
    return [
        {"@type": "OpeningHoursSpecification", "dayOfWeek": day, "opens": span.split("-")[0], "closes": span.split("-")[1]}
        for day, spans in hours.items()
        for span in spans
    ]


def place_jsonld(row: EventRow, details: dict[str, Any], cfg: PlaceInfoSettings, url: str, image: str | None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": _type(row.genres, cfg),
        "@id": url + "#place",
        "name": row.venue_name or row.title,
        "url": url,
        "image": image,
        "description": row.description or None,
        "telephone": details.get("phone"),
        "address": {"@type": "PostalAddress", "streetAddress": details.get("address"), "addressLocality": row.city, "addressCountry": "IL"}
        if details.get("address") else None,
        "geo": {"@type": "GeoCoordinates", "latitude": row.latitude, "longitude": row.longitude} if row.latitude is not None else None,
        "openingHoursSpecification": _hours_specs(details["hours"]) if details.get("hours") else None,
        "priceRange": "₪" * details["price_level"] if details.get("price_level") else None,
        "servesCuisine": details.get("cuisine"),
        "menu": details.get("menu_url"),
        "sameAs": [u for u in (details.get("website"), details.get("instagram")) if u] or None,
    }  # fmt: skip
    return {k: v for k, v in data.items() if v}


def place_html(details: dict[str, Any], lang: str) -> str:
    t, days = _TEXT[lang], _DAY_NAMES[lang]
    rows = []
    if details.get("rating"):
        rows.append(f"<p>★ {details['rating']} {t['rating']} ({details.get('reviews', 0)} {t['reviews']})</p>")
    if details.get("phone"):
        rows.append(f"<p>{t['phone']}: {escape(details['phone'])}</p>")
    if details.get("hours"):
        lines = "".join(f"<li>{days.get(d, d)}: {escape(', '.join(s))}</li>" for d, s in details["hours"].items())
        rows.append(f"<h2>{t['hours']}</h2><ul>{lines}</ul>")
    if details.get("ontopo_url"):
        rows.append(f'<p><a href="{escape(details["ontopo_url"])}" rel="nofollow noopener">{t["book"]}</a></p>')
    return "".join(rows)


def place_seo(session: Session, row: EventRow, cfg: PlaceInfoSettings, lang: str, url: str, image: str | None) -> tuple[dict[str, Any], str]:
    """(the page's LocalBusiness node, its extra body HTML)."""
    details = place_details(session, row, cfg, lang)
    return place_jsonld(row, details, cfg, url, image), place_html(details, lang)
