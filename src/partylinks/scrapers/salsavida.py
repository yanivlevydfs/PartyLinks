"""Salsa Vida (salsavida.com) - the weekly salsa and bachata nights of Israel, from a worldwide Latin dance calendar.

WordPress; robots.txt allows everything. The country's guide page (``guide_path``, ``/guides/israel/``) lists
every occurrence of the next 14 days as schema.org microdata cards (``article.event-card``): the series id
(``data-sv-source-event-id``), its page, name, start / end with their offset, the city, the address, the price
and the organizer's link (``offers``), the dance styles (``data-sv-card-families-*``) and an image. Its "load
more" button posts ``action=load_more_events`` to ``admin-ajax.php`` with the page's own nonce, category id,
start day and event type, and gets the next 14 days of the same cards (``data.html``, ``next_offset``,
``has_more``); windows are read up to ``horizon_days``, the site's own "next 3 months". The guide's map JSON
(``data-sv-map-items-json``) gives each series' price as written ("NIS 50", "20$") and its venue, address lines
and pin.

Each series' own page (one request per series a run) gives the venue (JSON-LD ``Event`` location: name, street,
coordinates), the organizer and the full text (``section.about-section``); when it fails, the map places it. Every night is a lesson then the party
("Class & Social"); a card with no social and no festival style is a class only and is not read.
Series another source reads at first hand, or closed to the public, are listed in ``skip_series``.
Artwork: most cards show the site's generic dancers picture (``placeholder_images``), never passed.
~300 nights over 90 days, ~16 requests a run.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from datetime import tzinfo
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse

from pydantic import Field
from selectolax.lexbor import LexborHTMLParser, LexborNode

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import (
    ParseError,
    clean_str,
    html_to_text,
    jsonld_nodes,
    parse_iso,
    schema_types,
    to_decimal,
    to_float,
    to_int,
    zone,
)
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_SIZE = re.compile(r"-\d+x\d+(?=\.\w+$)")  # WordPress thumbnail suffix: "logo-240x240.png" -> "logo.png"
_PAGE_VALUES = {
    "nonce": re.compile(r"ajaxNonce\s*=\s*'([0-9a-f]+)'"),
    "category": re.compile(r"currentCat\s*=\s*(\d+)"),
    "start": re.compile(r"selectedStartYmd\s*=\s*\"(\d{4}-\d\d-\d\d)\""),
    "event_type": re.compile(r"let eventType\s*=\s*'([a-z_]+)'"),
    "lang": re.compile(r"currentLang\s*=\s*\"([a-z]{2})\""),
    "window": re.compile(r"dayWindow\s*=\s*(\d+)"),
}


class SalsaVidaSettings(SourceSettings):
    site_url: str = "https://www.salsavida.com"
    guide_path: str = "/guides/israel/"
    ajax_path: str = "/wp-admin/admin-ajax.php"
    ajax_action: str = "load_more_events"
    # The site's own reach: "No matching events found in the next 90 days", "the entire series (next 3 months)".
    horizon_days: int = 90
    # Event page paths not read: series another source reads at first hand, or closed to the public.
    skip_series: list[str] = Field(default_factory=list)
    # The site's stand-in pictures (file name parts): a card showing one has no artwork.
    placeholder_images: list[str] = Field(default_factory=lambda: ["salsa-social-icon", "salsa-vida-default-og"])
    # A price label in another currency ("20$") makes the card's ILS price unreliable: no price then.
    foreign_currency_marks: list[str] = Field(default_factory=lambda: ["$", "€", "usd", "eur"])


class SalsaVidaScraper(BaseScraper[SalsaVidaSettings]):
    name = "salsavida"
    settings_model = SalsaVidaSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        page = await self.get_text(s.site_url + s.guide_path)
        guide = parse_guide(page)
        places = parse_map(page)
        series: dict[str, dict[str, Any]] = {}
        seen: set[str] = set()
        cards = parse_cards(page)
        offset = guide["window"]
        has_more = True
        while True:
            # Nearest first: each 14-day window is yielded before the next one is fetched.
            async for event in self._window(cards, series, places):
                if event.source_event_id not in seen:  # a night on the edge of two windows: once
                    seen.add(event.source_event_id)
                    yield event
            if not has_more or offset >= s.horizon_days or not guide["nonce"]:
                return
            data = await self.optional(f"window +{offset} days", self._more(guide, offset))
            if not data:
                return
            cards = parse_cards(data.get("html") or "")
            has_more = data.get("has_more") is not False
            following = to_int(data.get("next_offset")) or offset + guide["window"]
            if following <= offset:  # a window that does not move on would be asked for again and again
                has_more = False
            offset = following

    async def _more(self, guide: dict[str, Any], offset: int) -> dict[str, Any]:
        """One more window of cards, as the page's "load more" button asks for it."""
        s = self.settings
        form = {
            "action": s.ajax_action, "nonce": guide["nonce"], "mode": "append", "start_ymd": guide["start"],
            "offset": str(offset), "current_cat": guide["category"], "event_type": guide["event_type"],
            "route_dance_style": "", "salsa_styles": "", "lang": guide["lang"], "visitor_tz": "",
        }  # fmt: skip
        answer = await self.post_json(s.site_url + s.ajax_path, data=form)
        return parse_window(answer)

    async def _window(
        self, cards: list[dict[str, Any]], series: dict[str, dict[str, Any]], places: dict[str, dict[str, Any]]
    ) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        cards = [c for c in cards if not skipped(c["url"], s.skip_series)]
        for url in sorted({c["url"] for c in cards} - series.keys()):
            # A series page that fails costs its text; the guide's map still places its nights.
            page = await self.optional(f"series {url}", self.get_text(url))
            series[url] = parse_series(page) if page else {}
        events = []
        for card in cards:
            try:
                event = parse_occurrence(
                    card, series.get(card["url"], {}), places.get(card["url"], {}), s, self.timezone
                )
            except Exception as exc:  # noqa: BLE001 - one odd card never drops the others
                self.record_failure(f"card {card.get('id')} {card.get('start')}", exc)
                continue
            if event and is_current(event.start_at, event.end_at, RetentionSettings(), self.timezone):
                events.append(event)
        for event in sorted(events, key=lambda e: (e.start_at, e.source_event_id)):
            yield event


def skipped(url: str, skip_series: list[str]) -> bool:
    path = urlparse(url).path.rstrip("/")
    return any(path == p.rstrip("/") for p in skip_series)


def parse_guide(page_html: str) -> dict[str, Any]:
    """The guide page's "load more" values: nonce, category id, first day, event type, language, window days."""
    found = {key: (m.group(1) if (m := pattern.search(page_html)) else None) for key, pattern in _PAGE_VALUES.items()}
    if not found["category"] or not found["start"]:
        raise ParseError("salsavida: the guide page has no event list")
    return {
        "nonce": found["nonce"],
        "category": found["category"],
        "start": found["start"],
        "event_type": found["event_type"] or "social_classes",
        "lang": found["lang"] or "en",
        "window": int(found["window"] or 14),
    }


def parse_window(answer: Any) -> dict[str, Any]:
    """admin-ajax's answer -> {html, next_offset, has_more}; ``{}`` for a refusal (``success: false``)."""
    if not isinstance(answer, dict) or not answer.get("success"):
        return {}
    data = answer.get("data")
    return data if isinstance(data, dict) else {"html": data if isinstance(data, str) else ""}


def parse_map(page_html: str) -> dict[str, dict[str, Any]]:
    """The guide's map items: series page URL -> its venue, address, pin and price label as the site writes
    it ("NIS 60 / NIS 50 member", "20$", "Free"). The address is lines: venue, street, town, country."""
    node = LexborHTMLParser(page_html).css_first("script[data-sv-map-items-json]")
    try:
        items = json.loads(node.text()) if node else []
    except json.JSONDecodeError:
        return {}
    places = {}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("url"), str):
            continue
        lines = [line for raw in str(item.get("address") or "").splitlines() if (line := " ".join(raw.split()))]
        places[item["url"]] = {
            "cost": str(item.get("cost") or ""),
            "venue": lines[0] if len(lines) > 1 else None,
            "address": _address(", ".join(lines[1:])),
            "latitude": to_float(item.get("lat")),
            "longitude": to_float(item.get("lng")),
        }
    return places


def _meta(node: LexborNode, prop: str) -> str | None:
    found = node.css_first(f'meta[itemprop="{prop}"]')
    return clean_str(found.attributes.get("content")) if found else None


def parse_cards(fragment_html: str) -> list[dict[str, Any]]:
    """The guide's microdata cards -> one dict per night (its series id, page, name, times, place, price, styles)."""
    cards = []
    for node in LexborHTMLParser(fragment_html).css("article.event-card[data-sv-source-event-id]"):
        link = node.css_first('a[itemprop="url"]')
        title = node.css_first('h3[itemprop="name"]')  # the organizer block has an itemprop "name" of its own
        start = node.css_first('time[itemprop="startDate"]')
        place = node.css_first('[itemprop="location"]')
        offer = node.css_first('[itemprop="offers"]')
        organizer = node.css_first('[itemprop="organizer"]')
        image = node.css_first('img[itemprop="image"]')
        families = {
            kind: [f for f in (node.attributes.get(f"data-sv-card-families-{kind}") or "").split(",") if f.strip()]
            for kind in ("classes", "socials", "festivals")
        }
        cards.append({
            "id": node.attributes.get("data-sv-source-event-id") or "",
            "url": (link.attributes.get("href") or "") if link else "",
            "title": clean_str(title.text()) if title else None,
            "start": start.attributes.get("datetime") if start else None,
            "end": _meta(node, "endDate"),
            "city": _meta(place, "name") if place else None,
            "address": _meta(place, "address") if place else None,
            "description": _meta(node, "description"),
            "price": _meta(offer, "price") if offer else None,
            "currency": _meta(offer, "priceCurrency") if offer else None,
            "offer_url": _meta(offer, "url") if offer else None,
            "organizer": _meta(organizer, "name") if organizer else None,
            "image": (image.attributes.get("src") or "") if image else "",
            "families": families,
        })  # fmt: skip
    return cards


def parse_series(page_html: str) -> dict[str, Any]:
    """A series page -> its venue (name, street, town, coordinates), organizer and full text."""
    event = next((n for n in jsonld_nodes(page_html) if "event" in {t.lower() for t in schema_types(n)}), {})
    location = event.get("location") if isinstance(event.get("location"), dict) else {}
    address = location.get("address") if isinstance(location.get("address"), dict) else {}
    geo = location.get("geo") if isinstance(location.get("geo"), dict) else {}
    organizer = event.get("organizer") if isinstance(event.get("organizer"), dict) else {}
    # "<p class=about-text><p>...</p></p>": the parser closes the outer <p> at once, so the whole section is
    # read, less its "About this event" heading.
    about = LexborHTMLParser(page_html).css_first("section.about-section")
    if about is not None:
        for heading in about.css("h1, h2, h3, h4"):
            heading.decompose()
    return {
        "venue": clean_str(location.get("name")),
        "street": clean_str(address.get("streetAddress")),
        "city": clean_str(address.get("addressLocality")),
        "latitude": to_float(geo.get("latitude")),
        "longitude": to_float(geo.get("longitude")),
        "organizer": clean_str(organizer.get("name")),
        "description": html_to_text(about.html) if about else None,
    }


def _address(street: str | None) -> str | None:
    """ "3 Sharon,, Kfar Saba 44425, Israel" -> "3 Sharon, Kfar Saba 44425": no empty parts, no country."""
    parts = [" ".join(p.split()) for p in (street or "").split(",")]
    parts = [p for p in parts if p and p.casefold() != "israel"]
    return ", ".join(parts) or None


def _price(card: dict[str, Any], cost: str | None, s: SalsaVidaSettings) -> Decimal | None:
    """The card's price in shekels; none when the site's own label names another currency ("20$")."""
    if (card.get("currency") or "ILS").upper() != "ILS":
        return None
    if cost and any(mark in cost.casefold() for mark in s.foreign_currency_marks):
        return None
    return to_decimal(card.get("price"))


def _image(url: str, s: SalsaVidaSettings) -> str | None:
    if not url.startswith(("http://", "https://")) or any(p in url for p in s.placeholder_images):
        return None
    return _SIZE.sub("", url)


def parse_occurrence(
    card: dict[str, Any], series: dict[str, Any], place: dict[str, Any], s: SalsaVidaSettings, timezone: str
) -> ScrapedEvent | None:
    """One card -> its night; None without a series id, a page, a name or a start, and for a class only.
    The place is the series page's (``parse_series``), else the guide map's (``parse_map``), else the card's."""
    families = card["families"]
    if not card["id"] or not card["url"] or not card["title"] or not (families["socials"] or families["festivals"]):
        return None
    tz: tzinfo = zone(timezone)
    start_at = parse_iso(card["start"], timezone)
    if start_at is None:
        return None
    start_at = start_at.astimezone(tz)
    end_at = parse_iso(card["end"], timezone)
    end_at = end_at.astimezone(tz) if end_at and end_at > start_at else None
    styles = list(dict.fromkeys(f.strip().lower() for kind in families.values() for f in kind if f.strip()))
    price = _price(card, place.get("cost"), s)
    pins = [(p.get("latitude"), p.get("longitude")) for p in (series, place)]
    latitude, longitude = next(((lat, lng) for lat, lng in pins if lat is not None and lng is not None), (None, None))
    image = _image(card["image"], s)
    offer_url = card.get("offer_url")
    return ScrapedEvent(
        source=SalsaVidaScraper.name,
        source_event_id=f"{card['id']}:{start_at.date().isoformat()}",  # the site's own occurrence key
        url=card["url"],
        title=card["title"],
        description=series.get("description") or card.get("description"),
        start_at=start_at,
        end_at=end_at,
        venue_name=series.get("venue") or place.get("venue"),
        address=_address(series.get("street")) or place.get("address") or card.get("address"),
        city=series.get("city") or card.get("city"),
        latitude=latitude,
        longitude=longitude,
        source_genres=styles,
        organizer=series.get("organizer") or card.get("organizer"),
        images=[image] if image else [],
        min_price=price,
        max_price=price,
        extra={"purchase_url": offer_url} if offer_url and offer_url.startswith(("http://", "https://")) else {},
    )
