"""Radical (radical.org.il, "בית רדיקל") - culture house in Jaffa: lectures, films, concerts, workshops.

A WooCommerce shop (Elementor), one product per event, behind Cloudflare (403 for plain httpx, 200
for a Chrome TLS fingerprint). The calendar page ``/calendar/`` holds several Elementor loop grids
(all coming events, the auditorium, the basement, films, and the archive of past ones); each card
gives the product id, link, title, a date without a year ("יום שלישי, 17/11"), the start time and
artwork. A grid's "load more" pages are ``?e-page-<widget id>=N`` (``data-next-page``); they are
followed only while a page still has a coming date, so the archive grid is never paged. The Store
API (``/wp-json/wc/store/v1/products?include=...``) adds the categories, price range, stock and text
for those products in one call; it has no date, so it never lists events alone (past products stay
in stock there). ~6 requests a run. One building (two halls), so its place comes from the config.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import date, datetime
from typing import Any

from pydantic import Field
from selectolax.lexbor import LexborHTMLParser

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import clean_str, html_to_text, to_decimal, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_DAY = re.compile(r"(\d{1,2})/(\d{1,2})")
_WEEKDAY = re.compile(r"יום\s+(ראשון|שני|שלישי|רביעי|חמישי|שישי|שבת)")
_WEEKDAYS = {"שני": 0, "שלישי": 1, "רביעי": 2, "חמישי": 3, "שישי": 4, "שבת": 5, "ראשון": 6}
_TIME = re.compile(r"^(\d{1,2}):(\d{2})$")
_POST = re.compile(r"\bpost-(\d+)\b")
_RETENTION = RetentionSettings()


class RadicalSettings(SourceSettings):
    site_url: str
    calendar_path: str  # the calendar page; a grid's later pages are <calendar_path>?e-page-<id>=N
    products_path: str  # Store API products endpoint
    max_pages: int = Field(10, ge=1)  # per grid
    venue_name: str
    address: str
    city: str
    latitude: float | None = None
    longitude: float | None = None
    # Category slug -> hall name (an event in one of them gets it as extra["hall"]).
    halls: dict[str, str] = Field(default_factory=dict)
    # Category slugs / names that say where or that it is an event, not what kind ("אירועים", "אחר").
    skip_categories: list[str] = Field(default_factory=list)


class RadicalScraper(BaseScraper[RadicalSettings]):
    name = "radical"
    settings_model = RadicalSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        today = datetime.now(zone(self.timezone)).date()
        first = await self.get_text(s.site_url + s.calendar_path)
        cards: dict[str, dict[str, Any]] = {}
        grids = parse_calendar(first)
        for grid in grids.values():
            for card in grid["cards"]:
                cards.setdefault(card["id"], card)
        for widget, grid in grids.items():
            next_url, pages = grid["next"], 1
            current = any(_coming(c, today) for c in grid["cards"])
            while next_url and current and pages < s.max_pages:
                page = await self.optional(f"calendar {widget} page {pages + 1}", self.get_text(next_url))
                if page is None:
                    break
                pages += 1
                later = parse_calendar(page).get(widget) or {"cards": [], "next": None}
                for card in later["cards"]:
                    cards.setdefault(card["id"], card)
                next_url, current = later["next"], any(_coming(c, today) for c in later["cards"])
        coming = {k: c for k, c in cards.items() if _coming(c, today)}
        self.log.debug("listed %d cards, %d coming", len(cards), len(coming))

        products: dict[str, dict[str, Any]] = {}
        ids = list(coming)
        for start in range(0, len(ids), 100):
            batch = await self.optional(
                "store api",
                self.get_json(
                    s.site_url + s.products_path,
                    params={"include": ",".join(ids[start : start + 100]), "per_page": 100},
                    expect=list,
                ),
            )
            products.update({str(p.get("id")): p for p in batch or [] if isinstance(p, dict)})

        for card in coming.values():
            try:
                event = parse_event(card, products.get(card["id"]), s, self.timezone, today)
            except Exception as exc:  # noqa: BLE001 - one odd card never drops the others
                self.record_failure(f"event {card['url']}", exc)
                continue
            if event and is_current(
                event.start_at, event.end_at, _RETENTION, self.timezone, time_known=event.time_known
            ):
                yield event


def _coming(card: dict[str, Any], today: date) -> bool:
    day = card_day(card["date"], today)
    return day is not None and day >= today


def card_day(text: str, today: date) -> date | None:
    """ "יום שלישי, 17/11" -> the date in the year (of last, this or next) whose weekday matches, nearest
    today; without a weekday, the nearest one."""
    m = _DAY.search(text)
    if not m:
        return None
    day, month = int(m.group(1)), int(m.group(2))
    weekday = _WEEKDAY.search(text)
    options = []
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            options.append(date(year, month, day))
        except ValueError:
            continue
    if weekday:
        options = [d for d in options if d.weekday() == _WEEKDAYS[weekday.group(1)]] or options
    return min(options, key=lambda d: abs(d - today), default=None)


def parse_calendar(page_html: str) -> dict[str, dict[str, Any]]:
    """Calendar page -> {widget id: {"cards": [...], "next": next page url or None}}, one per loop grid.
    A card: id, url, title, date, time, image, category slugs (from its classes)."""
    grids: dict[str, dict[str, Any]] = {}
    for widget in LexborHTMLParser(page_html).css("div.elementor-widget-loop-grid"):
        widget_id = widget.attributes.get("data-id") or ""
        if widget_id in grids:
            continue
        more = widget.css_first("[data-next-page]")
        cards = []
        for item in widget.css("div.e-loop-item"):
            classes = item.attributes.get("class") or ""
            post, link = _POST.search(classes), item.css_first("a[href]")
            heading = item.css_first("h1, h2, h3, h4")
            if not post or link is None or heading is None:
                continue
            texts = [clean_str(n.text()) or "" for n in item.css(".elementor-widget-text-editor")]
            day = next((t for t in texts if _DAY.search(t)), "")
            clock = next((t for t in texts if _TIME.match(t)), "")
            image = item.css_first("img")
            cards.append(
                {
                    "id": post.group(1),
                    "url": link.attributes.get("href") or "",
                    "title": clean_str(heading.text()) or "",
                    "date": day,
                    "time": clock,
                    "image": (image.attributes.get("src") or "") if image else "",
                    "categories": [c[len("product_cat-") :] for c in classes.split() if c.startswith("product_cat-")],
                }
            )
        grids[widget_id] = {"cards": cards, "next": more.attributes.get("data-next-page") if more else None}
    return grids


def parse_event(
    card: dict[str, Any], product: dict[str, Any] | None, s: RadicalSettings, timezone: str, today: date
) -> ScrapedEvent | None:
    """A calendar card (+ its Store API product, if any) -> event; None without a date, title or link."""
    day = card_day(card["date"], today)
    if day is None or not card["title"] or not card["url"].startswith("http"):
        return None
    clock = _TIME.match(card["time"])
    hour, minute = (int(clock.group(1)), int(clock.group(2))) if clock else (0, 0)
    start_at = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone(timezone))

    product = product or {}
    categories = [
        (str(c.get("slug") or ""), clean_str(c.get("name")) or "")
        for c in product.get("categories") or []
        if isinstance(c, dict)
    ] or [(slug, "") for slug in card["categories"]]
    skip = {x.lower() for x in s.skip_categories}
    genres = [name.lower() for slug, name in categories if name and slug not in skip and name.lower() not in skip]
    halls = list(dict.fromkeys(s.halls[slug] for slug, _ in categories if slug in s.halls))

    prices = product.get("prices") or {}
    span = prices.get("price_range") or {}
    low = to_decimal(span.get("min_amount")) if span else to_decimal(prices.get("price"))
    high = to_decimal(span.get("max_amount")) if span else low
    images = [*((i or {}).get("src") for i in product.get("images") or []), card["image"]]
    return ScrapedEvent(
        source=RadicalScraper.name,
        source_event_id=card["id"],
        url=card["url"],
        title=card["title"],
        description=html_to_text(product.get("description")),
        start_at=start_at,
        time_known=clock is not None,
        venue_name=s.venue_name,
        address=s.address,
        city=s.city,
        latitude=s.latitude,
        longitude=s.longitude,
        source_genres=list(dict.fromkeys(genres)),
        images=list(dict.fromkeys(i for i in images if isinstance(i, str) and i.startswith("http"))),
        min_price=low,
        max_price=high,
        is_sold_out=product.get("is_in_stock") is False,
        tickets=[
            TicketTier(
                name="כרטיס",
                price=low,
                original_price=to_decimal(prices.get("regular_price")),
                is_free=low == 0,
            )
        ]
        if low is not None
        else [],
        extra={"purchase_url": card["url"], "hall": halls[0] if len(halls) == 1 else None},
    )
