"""Levontin 7 (levontin7.com) - live-music club on Levontin St., Tel Aviv.

WordPress + the FAT Event plugin. The home page carries every upcoming show (sliders and tabs, so each
appears more than once): ``.fat-event-item[data-event-id]`` with the start / end on its links
(``data-s`` / ``data-e``: Unix time of the Israeli wall clock read as UTC, so 20:00 is 20:00 here), the
artwork and a shortened title. Each show's page has the full
title, the text, the prices (``.fat-event-popup-fees``) and the Eventer ticket link. One venue: its
place comes from the config.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import datetime
from decimal import Decimal

from selectolax.lexbor import LexborHTMLParser

from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import clean_str, from_unix, html_to_text, to_decimal, zone
from partylinks.scrapers.base import BaseScraper, SourceSettings

_SIZE = re.compile(r"-\d+x\d+(?=\.\w+$)")  # "-1000x437.jpg": a thumbnail of the full image


class Levontin7Settings(SourceSettings):
    site_url: str
    listing_path: str = "/"
    venue_name: str
    address: str
    city: str
    latitude: float | None = None
    longitude: float | None = None


class Levontin7Scraper(BaseScraper[Levontin7Settings]):
    name = "levontin7"
    settings_model = Levontin7Settings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        cards = parse_listing(await self.get_text(s.site_url + s.listing_path))
        self.log.info("listed %d shows", len(cards))
        for card in cards:
            try:
                event = parse_card(card, s, self.timezone)
            except Exception as exc:  # noqa: BLE001 - one odd card never drops the others
                self.record_failure(f"show {card['url']}", exc)
                continue
            if event is None:
                continue
            page = await self.optional(f"page {card['url']}", self.get_text(card["url"]))
            if page:
                add_page(event, page)
            yield event


def parse_listing(page_html: str) -> list[dict[str, str]]:
    """Home page -> one dict per show (first card of each id): id, url, start, end, title, image."""
    cards: dict[str, dict[str, str]] = {}
    for node in LexborHTMLParser(page_html).css(".fat-event-item[data-event-id]"):
        event_id = node.attributes.get("data-event-id") or ""
        link = node.css_first(".fat-event-title a[data-s]") or node.css_first("a[data-s]")
        if not event_id or event_id in cards or link is None or not link.attributes.get("href"):
            continue
        image = node.css_first(".fat-event-thumb img")
        cards[event_id] = {
            "id": event_id,
            "url": link.attributes.get("href") or "",
            "start": link.attributes.get("data-s") or "",
            "end": link.attributes.get("data-e") or "",
            "title": (clean_str(link.text()) or "").rstrip("…").strip(),
            "image": _SIZE.sub("", image.attributes.get("src") or "") if image else "",
        }
    return list(cards.values())


def wall_clock(value: str, timezone: str) -> datetime | None:
    """The plugin's "Unix time": the local clock written as if UTC (data-s 20:00 UTC = the 20:00 show)."""
    utc = from_unix(value)
    return utc.replace(tzinfo=zone(timezone)) if utc else None


def parse_card(card: dict[str, str], s: Levontin7Settings, timezone: str) -> ScrapedEvent | None:
    """A home-page card -> event (title, text, prices and ticket link come from its page); None without a time."""
    start_at = wall_clock(card["start"], timezone)
    if start_at is None or not card["title"]:
        return None
    end_at = wall_clock(card["end"], timezone)
    return ScrapedEvent(
        source=Levontin7Scraper.name,
        source_event_id=card["id"],
        url=card["url"],
        title=card["title"],
        start_at=start_at,
        end_at=end_at if end_at and end_at > start_at else None,
        venue_name=s.venue_name,
        address=s.address,
        city=s.city,
        latitude=s.latitude,
        longitude=s.longitude,
        source_genres=["הופעה"],
        images=[card["image"]] if card["image"].startswith("http") else [],
    )


def add_page(event: ScrapedEvent, page_html: str) -> None:
    """The show's page: full title, text, prices and the Eventer ticket link."""
    tree = LexborHTMLParser(page_html)
    if (title := tree.css_first(".fat-event-title")) and (text := clean_str(title.text())):
        event.title = text
    if (body := tree.css_first(".fat-event-single-content")) and (text := html_to_text(body.html)):
        event.description = text
    prices = sorted(p for n in tree.css(".fat-event-popup-fees .price strong") if (p := to_decimal(n.text())) is not None)
    if prices:
        event.min_price, event.max_price = prices[0], prices[-1]
        event.tickets = [TicketTier(name="כרטיס", price=p, is_free=p == Decimal(0)) for p in prices]
    ticket = next(
        (href for a in tree.css("a[href]") if "eventer.co.il" in (href := a.attributes.get("href") or "")), None
    )
    if ticket:
        event.extra = {**(event.extra or {}), "purchase_url": ticket.split("?")[0]}
