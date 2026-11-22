"""Basalon (basalon.co.il) - a marketplace of small events: talks, stand-up, shows, plays, kids' shows and workshops,
many of them hosted in living rooms, pubs and studios nationwide.

WordPress (the EventList plugin, no REST route for its events). Read in two steps:

* The category pages (``/show/``, ``/lectures/``, ``/הופעות-סטנדאפ/`` ..., ``/page/N/``; robots.txt disallows
  ``?page=``), 45 cards a page, soonest first. A card gives the post id (``tagid_<id>``), the page link, the title
  and the next date ("היום ב- 11:00", "יום ו׳, 27.11 ב- 10:30", "15.1.27"). A card without a date is booked by
  appointment ("בתיאום", a quote by WhatsApp): not an event, so it is not read further. A post in several
  categories gets each category's label.
* The event page's schema.org ``Event`` (JSON-LD): the next date's start and end with the zone, the address
  (street + locality), the artwork, the text, the host (``organizer``) and the ticket rows (``offers``).

Not kept: gift cards (no Event on the page), private workshops (a ticket row "סדנה פרטית ..."), at the customer's
home ("בבית הלקוח") and pages whose address is a locality only (the host's home, sent after booking: no place to
pin). The site lists one date per event, its next one: the event id is the post and that date.
About one page request per dated card (~650 a run, Nov 2026; the pages are ~1 MB), at low concurrency.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import cache
from typing import Any
from urllib.parse import quote, urljoin

from pydantic import BaseModel, Field
from selectolax.lexbor import LexborHTMLParser

from partylinks.config import RetentionSettings, get_settings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import clean_str, html_to_text, jsonld_nodes, parse_iso, to_decimal, zone
from partylinks.places import PlaceIndex, all_cities
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_TAG_ID = re.compile(r"tagid_(\d+)")
_DAY_MONTH = re.compile(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?")


class BasalonCategory(BaseModel):
    path: str  # the category page (/show/), unquoted
    label: str  # its name, kept as the event's site category


class BasalonSettings(SourceSettings):
    site_url: str = "https://basalon.co.il"
    categories: list[BasalonCategory]
    max_pages: int = Field(20, ge=1)  # per category (the site shows at most 10)
    today_words: list[str] = Field(default_factory=lambda: ["היום"])
    tomorrow_words: list[str] = Field(default_factory=lambda: ["מחר"])
    # A ticket row naming one of these is a workshop for one party only.
    private_ticket_words: list[str] = Field(default_factory=lambda: ["סדנה פרטית", "פרטית ל"])
    # A place holding one of these is the customer's own home.
    home_words: list[str] = Field(default_factory=lambda: ["בבית הלקוח"])
    country_suffixes: list[str] = Field(default_factory=lambda: [", ישראל", ",ישראל", ", Israel"])


@dataclass
class Card:
    post_id: str
    url: str
    title: str
    when: date | None  # the card's next date (sorting only; the page gives the time)
    labels: list[str] = field(default_factory=list)


class BasalonScraper(BaseScraper[BasalonSettings]):
    name = "basalon"
    settings_model = BasalonSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        today = datetime.now(zone(self.timezone)).date()
        cards: dict[str, Card] = {}
        for category in s.categories:
            found = await self.optional(f"category {category.path}", self._list_category(category, today)) or []
            for card in found:
                kept = cards.setdefault(card.post_id, card)
                if category.label not in kept.labels:
                    kept.labels.append(category.label)
        if not cards and self.failures:
            raise RuntimeError(f"{self.name}: every category page failed")
        skip = [w.lower() for w in s.skip_title_words]
        wanted = [c for c in cards.values() if c.when and not any(w in c.title.lower() for w in skip)]
        wanted.sort(key=lambda c: c.when or date.max)  # soonest first
        self.log.debug("%d cards, %d dated and kept", len(cards), len(wanted))
        async for event in self.stream_isolated(wanted, self._fetch_event, label=lambda c: c.url):
            if is_current(event.start_at, event.end_at, RetentionSettings(), self.timezone):
                yield event

    async def _list_category(self, category: BasalonCategory, today: date) -> list[Card]:
        s = self.settings
        base = urljoin(s.site_url, quote(category.path))
        cards: dict[str, Card] = {}
        for page in range(1, s.max_pages + 1):
            url = base if page == 1 else f"{base}page/{page}/"
            page_html = await self.get_text(url) if page == 1 else await self.optional(f"{url}", self.get_text(url))
            new = [c for c in parse_cards(page_html or "", s, today) if c.post_id not in cards]
            if not new:
                break
            cards.update((c.post_id, c) for c in new)
        return list(cards.values())

    async def _fetch_event(self, card: Card) -> ScrapedEvent | None:
        return parse_event(await self.get_text(card.url), card, self.settings, self.timezone)


def card_date(text: str | None, s: BasalonSettings, today: date) -> date | None:
    """The card's next date: "היום ב- 11:00", "מחר", "יום ו׳, 27.11 ב- 10:30", "יום ו׳, 15.1.27 ב- 13:00"."""
    if not text:
        return None
    if any(w in text for w in s.today_words):
        return today
    if any(w in text for w in s.tomorrow_words):
        return today + timedelta(days=1)
    match = _DAY_MONTH.search(text)
    if not match:
        return None
    day, month, year = int(match[1]), int(match[2]), match[3]
    try:
        if year:
            return date(int(year) + (2000 if len(year) == 2 else 0), month, day)
        found = date(today.year, month, day)
    except ValueError:
        return None
    return found if found >= today - timedelta(days=1) else found.replace(year=today.year + 1)


def parse_cards(page_html: str, s: BasalonSettings, today: date) -> list[Card]:
    """One category page's cards, in the page's order."""
    cards = []
    for node in LexborHTMLParser(page_html).css("li.event_entry"):
        match = _TAG_ID.search(node.attributes.get("class") or "")
        link = node.css_first("a.second_font[href]")
        if not match or not link:
            continue
        title = clean_str(link.text())
        when = node.css_first(".bsl-cnd-date")
        if title:
            cards.append(
                Card(
                    post_id=match[1],
                    url=urljoin(s.site_url, link.attributes.get("href") or ""),
                    title=title,
                    when=card_date(when.text() if when else None, s, today),
                )
            )
    return cards


@cache
def _localities() -> PlaceIndex:
    enrichment = get_settings().enrichment
    return PlaceIndex(all_cities(enrichment.cities, enrichment.places_min_population))


def _offers(node: dict[str, Any]) -> list[dict[str, Any]]:
    offers = node.get("offers")
    return [o for o in (offers if isinstance(offers, list) else [offers]) if isinstance(o, dict)]


def parse_event(page_html: str, card: Card, s: BasalonSettings, timezone: str) -> ScrapedEvent | None:
    """The event page's schema.org Event -> its next date; None for a gift card, a private workshop, a show at the
    customer's home or a page with no street to pin."""
    node = next((n for n in jsonld_nodes(page_html) if n.get("@type") == "Event"), None)
    if node is None:
        return None
    start = parse_iso(node.get("startDate"), timezone)
    if start is None:
        return None
    offers = _offers(node)
    if any(w in str(o.get("name") or "") for o in offers for w in s.private_ticket_words):
        return None
    location = node.get("location") if isinstance(node.get("location"), dict) else {}
    address = location.get("address") if isinstance(location.get("address"), dict) else {}
    street, city = clean_str(address.get("streetAddress")), clean_str(address.get("addressLocality"))
    place = clean_str(location.get("name"))
    for suffix in s.country_suffixes:
        place = place.removesuffix(suffix) if place else place
        street = street.removesuffix(suffix) if street else street
    if any(w in f"{place} {street} {card.title}" for w in s.home_words) or not street:
        return None
    # The page's addressLocality is the place name's second comma part cut at a '"': "כנסיית בית החסד, שדרות
    # הפלי"ם, חיפה" came as street "כנסיית בית החסד", locality "שדרות הפלי" - filed under the town Sderot (5
    # events, Nov 2026). The name ends with the town: when that is a town, it is the city and the name the address.
    parts = [p.strip() for p in (place or "").split(",") if p.strip()]
    if len(parts) >= 2 and _localities().is_place(parts[-1]):
        city, street = parts[-1], ", ".join(parts[:-1])
    tickets = [
        TicketTier(name=name, price=price, is_free=price == 0)
        for o in offers
        if (name := clean_str(html_to_text(str(o.get("name") or ""))))
        and (price := to_decimal(o.get("price"))) is not None
    ]
    prices = [t.price for t in tickets if t.price is not None]
    organizer = node.get("organizer") if isinstance(node.get("organizer"), dict) else {}
    image = node.get("image")
    image = image.get("url") if isinstance(image, dict) else image[0] if isinstance(image, list) and image else image
    return ScrapedEvent(
        source=BasalonScraper.name,
        source_event_id=f"{card.post_id}:{start.date().isoformat()}",
        url=card.url,
        title=clean_str(html_to_text(str(node.get("name") or ""))) or card.title,
        description=html_to_text(str(node.get("description") or "")),
        start_at=start,
        end_at=parse_iso(node.get("endDate"), timezone),
        venue_name=place or street,
        address=f"{street}, {city}" if city and city not in street else street,
        city=city,
        source_genres=[label.lower() for label in card.labels],
        images=[image] if isinstance(image, str) and image.startswith("http") else [],
        tickets=tickets,
        currency="ILS",
        min_price=min(prices) if prices else None,
        max_price=max(prices) if prices else None,
        organizer=clean_str(organizer.get("name")),
    )
