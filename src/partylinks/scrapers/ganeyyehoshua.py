"""Ganei Yehoshua (park.co.il, "גני יהושע") - the Yarkon Park company's own events: birdwatching and nature
tours, family activities, sport and the park's festivals (Lumagica), in its parks across Tel Aviv: Ganei
Yehoshua (the Yarkon), Park Menachem Begin, Ganei HaTeva, Rosh Tzipor, the Farm, Goldberg's Orchard, Sportek.

The site is WordPress (its own theme; the events post type is not in wp-json). One request lists every
current event (``listing_path``, "אירועים"): each ``div.grid-item`` card carries the post id, its event type
and park as classes (``animals location-136``, named by the page's two filter menus), the title, the
subtitle, the artwork and the first dates. Each event's page (~25) adds every date and time (the dates field,
"05.12.26 09:00, 11:00"; sold-out times in the ticket form's ``data-tickets``), the prices ("מחיר לכרטיס",
the residents' "דיגיתל"), whether it is free, the text, where it meets ("היכן?") and, for events sold
elsewhere (Lumagica on Kupat Tel Aviv), the seller's link. One event per date and time.

The site gives no coordinates: the meeting point (the "היכן?" text and the park-map point) is matched to a
pin (``places``), else the park's own pin (``parks``); the parking directions are kept in ``extra``. Undated items (an open-air exhibition, a self-guided
sculpture trail) are not events. Residents-only ("בלעדי לדיגיטף") sessions and courses are skipped by title.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from html import unescape
from typing import Any
from urllib.parse import quote, urljoin

from pydantic import BaseModel, Field
from selectolax.lexbor import LexborHTMLParser, LexborNode

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import clean_str, html_to_text, to_decimal, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_SESSION = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})\s*\|?\s*((?:\d{1,2}:\d{2}(?:\s*,\s*)?)+)")
_CLOCK = re.compile(r"(\d{1,2}):(\d{2})")
_POINT = re.compile(r"[?&]point=([^\"'&]+)")


class GaneyYehoshuaPlace(BaseModel):
    # Words of the "היכן?" text / park-map point that name this spot ("ראש ציפור", "Rosh Tsipor").
    match: list[str]
    venue_name: str
    latitude: float
    longitude: float
    # Shown instead of the page's "היכן?" text when that is no address ("1373 מספר 1 תל-אביב-יפו").
    address: str | None = None


class GaneyYehoshuaPin(BaseModel):
    latitude: float
    longitude: float


class GaneyYehoshuaSettings(SourceSettings):
    site_url: str = "https://park.co.il"
    listing_path: str = "/%d7%90%d7%99%d7%a8%d7%95%d7%a2%d7%99%d7%9d/"  # /אירועים/
    city: str = "תל אביב-יפו"
    organizer: str = "גני יהושע"
    # Meeting points, first match wins; then the park the card names (``parks``, by its name).
    places: list[GaneyYehoshuaPlace] = Field(default_factory=list)
    parks: dict[str, GaneyYehoshuaPin] = Field(default_factory=dict)
    # Event-type class -> labels added beside the type's own name (the animals type is wildlife tours).
    type_genres: dict[str, list[str]] = Field(default_factory=dict)
    free_ticket_name: str = "כניסה חופשית"
    sold_out_mark: str = "sold out"


@dataclass
class ParkCard:
    post_id: str
    url: str
    title: str
    subtitle: str | None
    park: str | None
    event_type: str | None
    image: str | None
    sessions: list[datetime] = field(default_factory=list)


class GaneyYehoshuaScraper(BaseScraper[GaneyYehoshuaSettings]):
    name = "ganeyyehoshua"
    settings_model = GaneyYehoshuaSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        page = await self.get_text(s.site_url + s.listing_path)
        cards, types = parse_listing(page, s.site_url, self.timezone)
        self.log.debug("listed %d cards", len(cards))
        now = datetime.now(zone(self.timezone))
        # Nearest first: cards by their first date; an undated card is no event.
        dated = sorted((c for c in cards if c.sessions), key=lambda c: min(c.sessions))

        async def detail(card: ParkCard) -> list[ScrapedEvent]:
            return parse_event_page(await self.get_text(card.url), card, types, s, self.timezone)

        events: list[ScrapedEvent] = []
        async for batch in self.stream_isolated(dated, detail, lambda c: f"event {c.post_id}"):
            events.extend(batch)
        for event in sorted(events, key=lambda e: (e.start_at or now, e.source_event_id)):
            if is_current(event.start_at, event.end_at, RetentionSettings(), self.timezone):
                yield event


def parse_listing(page_html: str, site_url: str, timezone: str) -> tuple[list[ParkCard], dict[str, str]]:
    """The listing's cards, and the event-type filter's names (class -> "טבע וקיימות")."""
    tree = LexborHTMLParser(page_html)
    types: dict[str, str] = {}
    parks: dict[str, str] = {}
    for link in tree.css("nav a[data-filter]"):
        key, name = link.attributes.get("data-filter") or "", clean_str(link.text())
        if not name or key == "*":
            continue
        (parks if key.startswith("location-") else types)[key] = name
    cards = []
    for item in tree.css("div.grid-item[data-id]"):
        link = item.css_first(".park-event-title a")
        if link is None or not (href := link.attributes.get("href")):
            continue
        classes = (item.attributes.get("class") or "").split()
        park_node = item.css_first(".park-event-location")
        cards.append(
            ParkCard(
                post_id=str(item.attributes.get("data-id")),
                url=urljoin(site_url, href),
                title=clean_str(unescape(link.text())) or "",
                subtitle=clean_str(item.css_first(".park-event-desc").text())
                if item.css_first(".park-event-desc")
                else None,
                park=clean_str(park_node.text())
                if park_node
                else next((parks[c] for c in classes if c in parks), None),
                event_type=next((c for c in classes if c in types), None),
                image=_largest(item.css_first(".park-event-image img")),
                sessions=parse_sessions(" ".join(li.text() for li in item.css(".park-event-footer li")), timezone),
            )
        )
    return [c for c in cards if c.title], types


def parse_sessions(text: str, timezone: str) -> list[datetime]:
    """ "05.12.26 | 09:00, 11:00" (or without the bar, as the event page writes it) -> each date and time."""
    tz = zone(timezone)
    out = []
    for day, month, year, clocks in _SESSION.findall(text):
        y = int(year) + 2000 if len(year) == 2 else int(year)
        for hour, minute in _CLOCK.findall(clocks):
            out.append(datetime(y, int(month), int(day), int(hour), int(minute), tzinfo=tz))
    return sorted(set(out))


def parse_event_page(
    page_html: str, card: ParkCard, types: dict[str, str], s: GaneyYehoshuaSettings, timezone: str
) -> list[ScrapedEvent]:
    """One event's page -> one event per date and time it lists."""
    tree = LexborHTMLParser(page_html)
    sessions = parse_sessions(_field_text(tree, "dates.svg") or "", timezone) or card.sessions
    sold_out = _sold_out(tree, s, timezone)
    form = tree.css_first("#tickets-form")
    free = bool(form is not None and (form.attributes.get("data-is-free") or "").strip() == "1")
    tickets = _tickets(tree, free, s)
    prices = [t.price for t in tickets if t.price is not None]
    seller = tree.css_first(".events-ticket-section.external-event a[href]")
    where = tree.css_first(".where-to-title")
    where_text = clean_str(re.sub(r"^\s*היכן\?\s*", "", where.text(separator=" "))) if where else None
    point = _POINT.search(page_html)
    directions = _directions(tree)
    place = _place(" ".join(x for x in (where_text, point and unescape(point.group(1))) if x), s)
    pin = place or s.parks.get(card.park or "")
    genres = [types[card.event_type].lower()] if card.event_type in types else []
    genres += [g.lower() for g in s.type_genres.get(card.event_type or "", [])]
    images = [i for i in (card.image, _largest(tree.css_first(".park-single-event-image img"))) if i]
    extra: dict[str, Any] = {}
    if seller is not None and (href := seller.attributes.get("href", "")).startswith(("http://", "https://")):
        extra["purchase_url"] = href
    if card.subtitle:
        extra["subtitle"] = card.subtitle
    if directions:
        extra["directions"] = directions
    description = _description(tree)
    return [
        ScrapedEvent(
            source=GaneyYehoshuaScraper.name,
            source_event_id=f"{card.post_id}:{start:%Y%m%d%H%M}",
            url=card.url,
            title=card.title,
            description=description or card.subtitle,
            start_at=start,
            venue_name=place.venue_name if place else card.park,
            address=(place.address if place and place.address else None) or where_text,
            city=s.city,
            latitude=pin.latitude if pin else None,
            longitude=pin.longitude if pin else None,
            source_genres=list(dict.fromkeys(genres)),
            images=list(dict.fromkeys(images)),
            tickets=tickets,
            min_price=min(prices) if prices else None,
            max_price=max(prices) if prices else None,
            is_sold_out=start in sold_out,
            organizer=s.organizer,
            extra=extra,
        )
        for start in sessions
    ]


def _field(tree: LexborHTMLParser, icon: str) -> LexborNode | None:
    """The details field whose icon is ``icon`` ("dates.svg", "tickets.svg")."""
    for node in tree.css(".additional-details-field"):
        img = node.css_first(".field-icon-event img")
        if img is not None and (img.attributes.get("src") or "").endswith(icon):
            return node
    return None


def _field_text(tree: LexborHTMLParser, icon: str) -> str | None:
    node = _field(tree, icon)
    return node.text(separator=" ") if node is not None else None


def _tickets(tree: LexborHTMLParser, free: bool, s: GaneyYehoshuaSettings) -> list[TicketTier]:
    """ "מחיר לכרטיס: 60 ₪", "דיגיתל: 40 ₪" (the residents' price) -> a tier each; a free event one free tier."""
    if free:
        return [TicketTier(name=s.free_ticket_name, price=Decimal(0), is_free=True)]
    node = _field(tree, "tickets.svg")
    tiers = []
    for row in (c for c in node.iter() if c.tag == "div") if node is not None else []:  # its own rows, not itself
        label = row.css_first("span")
        name = clean_str((label.text() if label else "").rstrip(":"))
        price = to_decimal(re.sub(r"[^\d.]", "", (row.text() or "").replace(label.text() if label else "", "")))
        if name and price is not None:
            tiers.append(TicketTier(name=name, price=price))
    return tiers


def _sold_out(tree: LexborHTMLParser, s: GaneyYehoshuaSettings, timezone: str) -> set[datetime]:
    """The times the ticket form marks "09:30 - Sold Out"."""
    form = tree.css_first("#tickets-form")
    try:
        days = json.loads(form.attributes.get("data-tickets") or "{}") if form is not None else {}
    except ValueError:
        return set()
    out = set()
    for day in days.values() if isinstance(days, dict) else []:
        if not isinstance(day, dict):
            continue
        for clock in day.get("times") or []:
            if s.sold_out_mark in str(clock).lower():
                out.update(parse_sessions(f"{day.get('date')} {str(clock).split('-')[0].strip()}", timezone))
    return out


def _directions(tree: LexborHTMLParser) -> str | None:
    """The "הוראות הגעה:" line (text after its <h4>, up to the park-map link)."""
    box = tree.css_first(".events-where-to")
    if box is None:
        return None
    match = re.search(r"הוראות הגעה:\s*</h4>(.*?)(?:<div|$)", box.html or "", re.DOTALL)
    return clean_str(html_to_text(match.group(1))) if match else None


def _place(text: str, s: GaneyYehoshuaSettings) -> GaneyYehoshuaPlace | None:
    lowered = text.lower()
    return next((p for p in s.places if any(m.lower() in lowered for m in p.match)), None)


def _description(tree: LexborHTMLParser) -> str | None:
    node = tree.css_first(".single-event-content")
    if node is None:
        return None
    for drop in node.css(".events-where-to, script, style"):
        drop.decompose()
    return html_to_text(node.inner_html)


def _largest(img: LexborNode | None) -> str | None:
    """The image's widest ``srcset`` candidate (the original upload), else its ``src``."""
    if img is None:
        return None
    best, width = img.attributes.get("src"), 0
    for candidate in (img.attributes.get("srcset") or "").split(","):
        parts = candidate.strip().rsplit(" ", 1)
        if len(parts) == 2 and parts[1].endswith("w") and parts[1][:-1].isdigit() and int(parts[1][:-1]) > width:
            best, width = parts[0], int(parts[1][:-1])
    # Hebrew file names ("זהב-דיגיתל-....jpg") percent-encoded, as a browser sends them.
    return quote(best, safe=":/%?=&") if best and best.startswith(("http://", "https://")) else None
