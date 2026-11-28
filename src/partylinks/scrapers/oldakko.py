"""Old Akko (akko.org.il, "עכו העתיקה"): the Old Akko and Nazareth development company's site, its
events in the Knights' Halls, the citadel and the Crusader courtyard (quiet evening hours, Hanukkah-
Christmas, the sound-and-light show, festivals).

The events page (``/events/``) holds a card per coming event: title, a line of text, the date line
("5,6,7/12/2026"), the hours ("17:00-19:00"), the price ("10 ש\"ח") and the quick-buy link. The
WordPress API (``/wp-json/wp/v2/event``, the ``event`` post type: 71 posts, one call) gives each post's
text; the posts edited in the last ``recent_days`` that no card shows are read too, their dates from
the text ("📅 18-21.12.25, בכל ערב בין השעות 17:30-23:00"). A card or post that names no date (the
all-year audio guide, a show "coming soon") is no event. Each event's page adds the picture
(``og:image``). The site has no venue field: the halls the text names map to a venue in config
(``venues``), else Old Akko.

Not read: attractions, routes, culinary, around-akko, hotels (no dated events: places and their
opening hours); the translations (/en/, /ar/, /ru/...: the same events); the post / page sitemaps
(company news, tenders).
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import unquote

from pydantic import BaseModel, Field
from selectolax.lexbor import LexborHTMLParser, LexborNode

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import clean_str, html_to_text, og_image, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.bgalil import find_dates
from partylinks.scrapers.text_prices import says_free, text_tickets

_DAY_LIST = re.compile(r"(?<![\d/.])(\d{1,2}(?:\s*,\s*\d{1,2})+)\s*([./]\d{1,2}(?:[./]\d{2,4})?)")
_CLOCK = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?:\s*[-–]\s*(\d{1,2}):(\d{2}))?")
_PRICE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:₪|ש[\"״']ח)")


class AkkoVenue(BaseModel):
    name: str
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class OldAkkoSettings(SourceSettings):
    site_url: str = "https://www.akko.org.il"
    events_path: str = "/events/"
    api_path: str = "/wp-json/wp/v2/event"
    recent_days: int = 120  # posts edited this recently are read even when no card shows them
    city: str = "עכו"
    ticket_label: str = "כרטיס"  # a priced entry's tier name
    # A phrase in the title or text (lower case) -> the venue; the first that matches wins.
    venues: dict[str, AkkoVenue] = Field(default_factory=dict)
    default_venue: AkkoVenue = AkkoVenue(name="עכו העתיקה")
    organizer: str = "החברה לפיתוח עכו העתיקה"


@dataclass
class AkkoCard:
    url: str
    title: str
    text: str | None
    when: str | None  # the date line ("5,6,7/12/2026") or a line of text
    hours: str | None
    price: str | None
    buy_url: str | None
    image: str | None


class OldAkkoScraper(BaseScraper[OldAkkoSettings]):
    name = "oldakko"
    settings_model = OldAkkoSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        listing = await self.get_text(s.site_url + s.events_path)
        cards = {_key(c.url): c for c in parse_cards(listing)}
        since = (datetime.now(zone(self.timezone)) - timedelta(days=s.recent_days)).strftime("%Y-%m-%dT00:00:00")
        params = {"per_page": "100", "modified_after": since, "_fields": "id,link,title,content,modified"}
        posts = await self.optional("event API", self.get_json(s.site_url + s.api_path, params=params))
        by_url = {_key(str(p.get("link") or "")): p for p in posts or [] if isinstance(p, dict)}
        today = datetime.now(zone(self.timezone)).date()
        retention = RetentionSettings()
        keys = list(cards) + [k for k in by_url if k not in cards]

        async def one(key: str) -> list[ScrapedEvent]:
            card, post = cards.get(key), by_url.get(key)
            url = card.url if card else str(post.get("link")) if post else key
            page = await self.optional(f"page {url}", self.get_text(url))
            return parse_event(card, post, og_image(page) if page else None, s, self.timezone, today)

        async for events in self.stream_isolated(keys, one, lambda k: f"event {k}"):
            for event in events:
                if is_current(event.start_at, event.end_at, retention, self.timezone):
                    yield event


def _key(url: str) -> str:
    return url.split("?")[0].rstrip("/").lower()


def _text(node: LexborNode | None) -> str | None:
    return clean_str(" ".join(node.text().split())) if node else None


def parse_cards(html: str) -> list[AkkoCard]:
    """The event cards of the events page (the slider repeats a card: kept once)."""
    tree = LexborHTMLParser(html)
    out: dict[str, AkkoCard] = {}
    for card in tree.css(".card.card-featured, .card.card-event"):
        link = card.css_first("a.btn-read-more")
        title = _text(card.css_first(".card-title"))
        url = clean_str(link.attributes.get("href")) if link else None
        if not url or not title or "/event/" not in url or _key(url) in out:
            continue
        buy = card.css_first('a.btn-primary[href*="payment_"]')
        img = card.css_first("img.card-image")
        image = None
        if img is not None:
            image = clean_str(img.attributes.get("src") or img.attributes.get("data-src"))
            sources = [clean_str(s.attributes.get("srcset")) for s in card.css(".ratio source")]
            image = next((s for s in sources if s), image)  # the widest copy comes first
        out[_key(url)] = AkkoCard(
            url=url,
            title=title,
            text=_text(card.css_first(".card-text")),
            when=_text(card.css_first(".card-extra-first")),
            hours=_text(card.css_first(".card-extra-second")),
            price=_text(card.css_first(".package-price")),
            buy_url=clean_str(buy.attributes.get("href")) if buy else None,
            image=image,
        )
    return list(out.values())


def _days(text: str | None, today: date, *, cued: bool) -> list[date]:
    """The dates a line names; "5,6,7/12/2026" is three of them."""
    if not text:
        return []
    spelled = _DAY_LIST.sub(lambda m: " ".join(f"{d.strip()}{m.group(2)}" for d in m.group(1).split(",")), text)
    return find_dates(spelled, today, cued=cued)


def _hours(text: str | None) -> tuple[time | None, time | None]:
    m = _CLOCK.search(text or "")
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None, None
    end = (
        time(int(m.group(3)), int(m.group(4))) if m.group(3) and int(m.group(3)) < 24 and int(m.group(4)) < 60 else None
    )
    return time(int(m.group(1)), int(m.group(2))), end


def _venue(text: str, s: OldAkkoSettings) -> AkkoVenue:
    low = text.lower()
    return next((v for phrase, v in s.venues.items() if phrase in low), s.default_venue)


def parse_event(
    card: AkkoCard | None,
    post: dict[str, Any] | None,
    image: str | None,
    s: OldAkkoSettings,
    timezone: str,
    today: date,
) -> list[ScrapedEvent]:
    """One event per date: the card's date line first, else the dates in the post's text; none when
    neither names a date."""
    content = html_to_text(str((post.get("content") or {}).get("rendered") or "")) if post else None
    title = card.title if card else html_to_text(str((post or {}).get("title", {}).get("rendered") or "")) or ""
    if not title:
        return []
    days = _days(card.when, today, cued=False) if card else []
    clock_text = (card.hours or card.when or "") if card else ""
    if not days:
        days = _days(content, today, cued=True) or _days(title, today, cued=False)
        clock_text = clock_text or content or ""
    if not days:
        return []
    start_clock, end_clock = _hours(clock_text)
    if start_clock is None and card:
        start_clock, end_clock = _hours(card.text)  # "סיור עצמאי באולמות בין השעות 17:00-19:00"
    description = "\n\n".join(p for p in (card.text if card else None, content) if p) or None
    venue = _venue(" ".join(p for p in (title, description) if p), s)
    tiers: list[TicketTier] = []
    if card and card.price and (m := _PRICE.search(card.price)):
        tiers = [TicketTier(name=s.ticket_label, price=Decimal(m.group(1)))]
    elif content:
        tiers = text_tickets(content)
    url = card.url if card else str((post or {}).get("link"))
    # The page's slug, never the WordPress post id: the post comes from an optional call, and a run without it
    # would key every event anew (duplicates, unlisted rows).
    item_id = unquote(_key(url).rsplit("/", 1)[-1])[:60]
    images = [i for i in (image, card.image if card else None) if i]
    tz = zone(timezone)
    events = []
    for day in days:
        start = datetime.combine(day, start_clock or time(0), tz)
        end = datetime.combine(day, end_clock, tz) if end_clock else None
        if end is not None and end <= start:
            end += timedelta(days=1)
        events.append(
            ScrapedEvent(
                source="oldakko",
                source_event_id=f"{item_id}-{day.isoformat()}",
                url=url,
                title=title,
                description=description,
                start_at=start,
                end_at=end,
                time_known=start_clock is not None,
                venue_name=venue.name,
                address=venue.address,
                city=s.city,
                latitude=venue.latitude,
                longitude=venue.longitude,
                source_genres=[],  # the site has no categories: the title and text name the genre
                organizer=s.organizer,
                images=list(dict.fromkeys(images)),
                tickets=tiers,
                has_free_entry=says_free(description),
                extra={"purchase_url": card.buy_url} if card and card.buy_url else {},
            )
        )
    return events
