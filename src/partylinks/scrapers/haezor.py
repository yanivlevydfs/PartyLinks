"""HaEzor / The Zone (haezor.com) - live-music club at HaRechev 13, Tel Aviv.

A WordPress theme. The gig calendar page ("לוח הופעות") lists every upcoming show as a
``div.clsItem``: link, date without a year ("28 באוקטובר יום רביעי"), title, a one-line pitch, door
time and the prices ("80/90 ש"ח", "249-599 ש"ח"). Each show's own page adds the artwork, the genre
("מוזיקה לטינית"), the full text and the ticket link (Cardcom). One venue: its place comes from the config.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from decimal import Decimal

from selectolax.lexbor import LexborHTMLParser

from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import clean_str, html_to_text, zone
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.livetickets import HEBREW_MONTHS

_DATE = re.compile(r"(\d{1,2})\s+ב?(" + "|".join(HEBREW_MONTHS) + r")")
_TIME = re.compile(r"(\d{1,2}):(\d{2})")
_PRICE = re.compile(r"\d+(?:\.\d+)?")
_IMAGE = re.compile(r"url\(['\"]?([^'\")]+)")


class HaEzorSettings(SourceSettings):
    site_url: str
    listing_path: str
    venue_name: str
    address: str
    city: str
    latitude: float | None = None
    longitude: float | None = None


class HaEzorScraper(BaseScraper[HaEzorSettings]):
    name = "haezor"
    settings_model = HaEzorSettings

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
    """Gig calendar -> one dict per show: url, date, title, pitch, time, price."""
    cards = []
    for node in LexborHTMLParser(page_html).css("div.clsEvents div.clsItem"):
        link = node.css_first("h2 a")
        url = link.attributes.get("href") if link else None
        if link is None or not url:
            continue
        text = {cls: clean_str(n.text()) or "" for cls in ("clsDate", "clsDesc", "clsTime", "clsPrice") if (n := node.css_first(f".{cls}"))}
        cards.append({
            "url": url, "title": clean_str(link.text()) or "", "date": text.get("clsDate", ""),
            "pitch": text.get("clsDesc", ""), "time": text.get("clsTime", ""), "price": text.get("clsPrice", ""),
        })  # fmt: skip
    return cards


def show_day(text: str, today: date) -> date | None:
    """"28 באוקטובר יום רביעי" -> its date: this year, or next year once it is long past ("5 בינואר" in October)."""
    m = _DATE.search(text)
    if not m:
        return None
    day = date(today.year, HEBREW_MONTHS[m.group(2)], int(m.group(1)))
    return day.replace(year=today.year + 1) if day < today - timedelta(60) else day


def parse_card(card: dict[str, str], s: HaEzorSettings, timezone: str, today: date | None = None) -> ScrapedEvent | None:
    """A calendar row -> event (artwork, genre and ticket link come from its page); None without date or title."""
    tz = zone(timezone)
    day = show_day(card["date"], today or datetime.now(tz).date())
    if day is None or not card["title"]:
        return None
    clock = _TIME.search(card["time"])
    hour, minute = (int(clock[1]), int(clock[2])) if clock else (0, 0)
    prices = sorted(Decimal(p) for p in _PRICE.findall(card["price"]))
    return ScrapedEvent(
        source=HaEzorScraper.name,
        source_event_id=card["url"].rstrip("/").rsplit("/", 1)[-1],
        url=card["url"],
        title=card["title"],
        description=card["pitch"] or None,
        start_at=datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz),
        venue_name=s.venue_name,
        address=s.address,
        city=s.city,
        latitude=s.latitude,
        longitude=s.longitude,
        source_genres=["הופעה"],
        min_price=prices[0] if prices else None,
        max_price=prices[-1] if prices else None,
        # "80/90": the early / door price (or price tiers); one tier per amount.
        tickets=[TicketTier(name="כרטיס", price=p, is_free=p == 0) for p in prices],
        extra={"doors_open": clock.group(0) if clock else None},
    )


def add_page(event: ScrapedEvent, page_html: str) -> None:
    """The show's page: artwork, genre, full text and the ticket link."""
    tree = LexborHTMLParser(page_html)
    image = next(
        (m.group(1) for n in tree.css(".clsBG") if (m := _IMAGE.search(n.attributes.get("style") or ""))), None
    )
    if image and image.startswith("http"):
        event.images = [image]
    if genre := clean_str(t.text()) if (t := tree.css_first(".clsName")) else None:
        event.source_genres = [genre]
    if (article := tree.css_first(".clsBody article")) and (text := html_to_text(article.html)):
        event.description = text
    if (buy := tree.css_first("a.clsBuyLink")) and (href := buy.attributes.get("href")) and href.startswith("http"):
        event.extra = {**(event.extra or {}), "purchase_url": href}
