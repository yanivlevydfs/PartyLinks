"""Jerusalem municipality events (jerusalem.muni.il/he/experience/lobbyevents) - the city's calendar.

The calendar is an Umbraco site behind Akamai. Its page posts its filters to
``/Umbraco/Surface/Category/GetEventsNew/`` (form fields + the page's antiforgery token and cookie)
and gets HTML cards back. The page size it sends is ignored: one request for a date range returns
every occurrence in it (~1,200 for a year, ~420 events, Nov 2026). Each card is one date of one
event: title ("מנהרות הכותל | סיור"), day.month, "HH:MM - HH:MM", hall, image, free / accessible
icons. The categories are filters only, so each one (event type, topic, audience) is asked once
more and its label goes to the cards it returns; the "online" filter marks the Zoom ones, skipped.

Each event's page (one per event, not per date) adds the description, the map pin
(``maps?q=lat,lng``), the full and the Jerusalem-club price and the ticket link (``#a6``: Bimot,
the theatre's box office); without a price row, the price or free entry its text writes (text_prices).
Cards carry no year: a day.month before today is next year's.

Akamai refuses Railway's IP and plain clients: Chrome TLS through our Israeli VPN.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urljoin, urlsplit

from pydantic import Field
from selectolax.lexbor import LexborHTMLParser, LexborNode

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import ParseError, clean_str, html_to_text, to_decimal, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.text_prices import text_tickets


class JerusalemMuniSettings(SourceSettings):
    site_url: str = "https://www.jerusalem.muni.il"
    page_path: str = "/he/experience/lobbyevents/"
    list_path: str = "/Umbraco/Surface/Category/GetEventsNew/"
    parent_node_id: int = 1465
    node_id: int = 3096
    culture: str = "he-IL"
    city: str = "ירושלים"
    days_ahead: int = 365
    # Filter field -> {the site's value: its label}; each is one more listing request.
    categories: dict[str, dict[str, str]] = Field(default_factory=dict)
    ticket_words: list[str] = Field(default_factory=lambda: ["כרטיס", "הרשמה", "להרשמה", "הירשמו", "רכישה"])
    # Links in the body that are not a ticket seller (club benefit, chat, maps, social profiles).
    not_ticket_hosts: list[str] = Field(
        default_factory=lambda: ["jerusalem.muni.il", "wa.me", "whatsapp.com", "facebook.com", "instagram.com",
                                 "google.com", "waze.com", "youtube.com", "tiktok.com"]
    )  # fmt: skip


@dataclass
class Card:
    path: str  # /he/experience/allevents/<slug>/?occ=<n>
    title: str
    day: date
    start: str | None  # "HH:MM"
    end: str | None
    venue: str | None
    teaser: str | None
    image: str | None
    free: bool
    genres: list[str] = field(default_factory=list)

    @property
    def page(self) -> str:
        return self.path.split("?")[0]


@dataclass
class Page:
    description: str | None
    venue: str | None
    latitude: float | None
    longitude: float | None
    purchase_url: str | None
    tiers: list[TicketTier]
    free: bool


class JerusalemMuniScraper(BaseScraper[JerusalemMuniSettings]):
    name = "jerusalemmuni"
    settings_model = JerusalemMuniSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        page = s.site_url + s.page_path
        token = _token(await self.get_text(page))
        today = datetime.now(zone(self.timezone)).date()

        async def listing(**filters: str) -> list[Card]:
            form = {
                "pageNumber": "1", "itemsPerPage": "5000", "parentNodeId": str(s.parent_node_id), "search": "",
                "cat": "", "tar": "", "interest": "", "eventarea": "", "hood": "",
                "start": today.strftime("%d/%m/%Y"), "end": (today + timedelta(days=s.days_ahead)).strftime("%d/%m/%Y"),
                "access": "false", "free": "false", "online": "false", "bonus": "false", "first": "false",
                "catlobby": "false", "culture": s.culture, "curNode": str(s.node_id),
            } | filters  # fmt: skip
            response = await self.request(
                "POST", s.site_url + s.list_path, data=form,
                headers={"X-CSRF-TOKEN": token, "X-Requested-With": "XMLHttpRequest", "Referer": page},
            )  # fmt: skip
            return parse_cards(response.text, s.site_url, today)

        cards = {c.path: c for c in await listing()}
        if not cards:
            raise ParseError("the calendar answered without events")
        for kind, values in s.categories.items():
            for value, label in values.items():
                for card in await self.optional(f"category {kind}={value}", listing(**{kind: value + ","})) or []:
                    if card.path in cards and label not in cards[card.path].genres:
                        cards[card.path].genres.append(label)
        online = {c.path for c in await self.optional("online", listing(online="true")) or []}
        coming = [c for c in cards.values() if c.path not in online]

        by_page: dict[str, list[Card]] = {}
        for card in coming:
            by_page.setdefault(card.page, []).append(card)

        async def event_page(path: str) -> list[ScrapedEvent]:
            info = parse_page(await self.get_text(s.site_url + path), s)
            return [e for c in by_page[path] if (e := build_event(c, info, s, self.timezone))]

        async for events in self.stream_isolated(by_page, event_page, lambda p: p):
            for event in events:
                if is_current(event.start_at, event.end_at, RetentionSettings(), self.timezone):
                    yield event


def _token(html: str) -> str:
    found = re.search(r'name="__RequestVerificationToken" type="hidden" value="([^"]+)"', html)
    if not found:
        raise ParseError("the calendar page has no antiforgery token")
    return found.group(1)


_CLOCK = re.compile(r"(\d{1,2}:\d{2})(?:\s*-\s*(\d{1,2}:\d{2}))?")
_DAY = re.compile(r"(\d{1,2})\.(\d{1,2})")
_LOCATION = re.compile(r"/he/experience/allevents/[^'\"]+")


def _day(text: str, today: date) -> date | None:
    """ "08.11" -> the next 8 November from today (the listing starts today and spans a year)."""
    found = _DAY.search(text)
    if not found:
        return None
    day, month = int(found.group(1)), int(found.group(2))
    try:
        candidate = date(today.year, month, day)
    except ValueError:  # 29.02 in a common year
        candidate = date(today.year + 1, month, day)
    return candidate if candidate >= today else candidate.replace(year=today.year + 1)


def parse_cards(html: str, site_url: str, today: date) -> list[Card]:
    """The listing's cards, one per date of an event (the page repeats some in a list view)."""
    cards: dict[str, Card] = {}
    for node in LexborHTMLParser(html).css("div.cardItem"):
        link = _LOCATION.search(node.attributes.get("onclick") or "")
        title = clean_str(_text(node.css_first(".card-title")))
        day = _day(_text(node.css_first(".date .big")), today)
        if not link or not title or not day or link.group(0) in cards:
            continue
        clock = _CLOCK.search(_icon(node, "timeW"))
        image = node.css_first("figure img")
        icons = {(i.attributes.get("alt") or "").lower() for i in node.css(".infoIcons img")}
        cards[link.group(0)] = Card(
            path=link.group(0),
            title=title,
            day=day,
            start=clock.group(1) if clock else None,
            end=clock.group(2) if clock else None,
            venue=clean_str(_icon(node, "locationW")) or None,
            teaser=clean_str(_text(node.css_first(".card-text"))) or None,
            image=urljoin(site_url, image.attributes["src"]) if image and image.attributes.get("src") else None,
            free="free" in icons,
        )
    return list(cards.values())


def _text(node: LexborNode | None) -> str:
    return node.text(separator=" ", strip=True) if node else ""


def _icon(card: LexborNode, name: str) -> str:
    """The line a meta icon (<i class="timeW">) stands in."""
    icon = card.css_first(f"i.{name}")
    return _text(icon.parent) if icon is not None else ""


_PIN = re.compile(r"maps\?q=(-?\d+\.\d+),(-?\d+\.\d+)")
_SHEKELS = re.compile(r"₪\s*(\d+(?:\.\d+)?)")


def parse_page(html: str, s: JerusalemMuniSettings) -> Page:
    """An event's own page: its text, pin, prices and ticket link."""
    tree = LexborHTMLParser(html)
    about = tree.css_first(".eventItemAbout .container")
    if about is None:
        raise ParseError("event page without its text")
    meta = about.css_first(".eventHeadMeta")
    venue = next(
        (
            clean_str(_text(i.parent))
            for i in (meta.css("img") if meta else [])
            if "eventItem-4" in (i.attributes.get("src") or "")
        ),
        None,
    )
    meta_text = _text(meta)
    full = next(
        (float(m) for m in _SHEKELS.findall(" ".join(_text(n) for n in about.css("[style*=line-through]")))), None
    )
    club = next((float(m) for m in _SHEKELS.findall(meta_text)), None)
    tiers: list[TicketTier] = []
    if full is not None:
        tiers.append(TicketTier(name="מחיר מלא", price=to_decimal(full)))
    if club is not None and club != full:
        tiers.append(TicketTier(name="חברי מועדון ירושלמי", price=to_decimal(club)))
    free = "חינם" in meta_text
    if free and not tiers:
        tiers.append(TicketTier(name="כניסה חופשית", price=to_decimal(0), is_free=True))

    # A link that says tickets / registration; the link block (#a6) also holds "read more" ones.
    link = None
    for anchor in (a for a in about.css("a") if any(w in _text(a) for w in s.ticket_words)):
        href = anchor.attributes.get("href") or ""
        host = urlsplit(href).hostname or ""
        if href.startswith("http") and not any(host == h or host.endswith("." + h) for h in s.not_ticket_hosts):
            link = href
            break

    for node in [meta, *about.css("h1, h2, #a6")]:
        if node is not None:
            node.decompose()
    pin = _PIN.search(html)
    return Page(
        description=html_to_text(about.html) or None,
        venue=venue,
        latitude=float(pin.group(1)) if pin else None,
        longitude=float(pin.group(2)) if pin else None,
        purchase_url=link,
        tiers=tiers,
        free=free,
    )


def _at(day: date, clock: str | None, timezone: str) -> datetime:
    hour, minute = (int(x) for x in (clock or "00:00").split(":"))
    return datetime(day.year, day.month, day.day, hour % 24, minute, tzinfo=zone(timezone))


def build_event(card: Card, page: Page, s: JerusalemMuniSettings, timezone: str) -> ScrapedEvent | None:
    start = _at(card.day, card.start, timezone)
    end = _at(card.day, card.end, timezone) if card.end else None
    if end and end <= start:
        end += timedelta(days=1)  # "22:00 - 02:00"
    # The pin is the page's hall; a date at another hall goes by its name.
    same_hall = page.venue is None or card.venue is None or page.venue == card.venue
    # No price row: the price or free entry its text writes ("עלות השתתפות: 37 ש\"ח", "ללא תשלום").
    free = [TicketTier(name="כניסה חופשית", price=to_decimal(0), is_free=True)] if card.free else []
    tiers = page.tiers or free or text_tickets(page.description)
    prices = [t.price for t in tiers if t.price is not None]
    data: dict[str, Any] = {"purchase_url": page.purchase_url} if page.purchase_url else {}
    return ScrapedEvent(
        source=JerusalemMuniScraper.name,
        source_event_id=f"{card.page.rstrip('/').rsplit('/', 1)[-1]}-{start:%Y%m%d%H%M}",
        url=s.site_url + card.path,
        title=card.title,
        description=page.description or card.teaser,
        start_at=start,
        end_at=end,
        time_known=card.start is not None,
        venue_name=card.venue or page.venue,
        city=s.city,
        latitude=page.latitude if same_hall else None,
        longitude=page.longitude if same_hall else None,
        source_genres=[g.lower() for g in card.genres],
        organizer=f"עיריית {s.city}",
        image_url=card.image,
        min_price=min(prices) if prices else None,
        max_price=max(prices) if prices else None,
        tickets=tiers,
        extra=data,
    )
