"""GoShow (goshow.co.il) - box office of Tel Aviv's small stages: AMAMA, Stand Up Factory, Comedy Bar,
Bar Giora, Enav, Beit HaMechulot, pubs and lecture halls across Israel (stand-up, jazz, talks, music).

Old ASP-style site, no API or JSON-LD. The home page lists every upcoming show (~280) as a card
(``li.carousel_row``) of its next date only: ``/event/<show id>/<date id>/<slug>``, a shortened title,
"venue, city" and "יום ... dd-mm-yy בשעה HH:MM". Cards dated before today are skipped there. Each
date's page (``/show/<show id>/<date id>/``, where /event/ redirects) gives the full title (og:title),
artwork (og:image), the text (``.showDesc``), the web and box-office prices, the venue's map point
(a Google Maps embed), the producer ("לאירועים נוספים של ...") and the producer's other dates
(``.moreShows li``: dd/mm/yyyy, HH:MM, title, "venue , city", price, /event/ link). Those dates (~650 in all) are events too, with their show's page details
when that page was read, at no extra request. The category pages (``/event/category/<slug>``, the
same cards) give each show its site category. robots.txt is empty.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import date, datetime, time
from typing import Any
from urllib.parse import urljoin

from pydantic import Field
from selectolax.lexbor import LexborHTMLParser

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import clean_str, html_to_text, to_decimal, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_CARD_DATE = re.compile(r"(\d{1,2})-(\d{1,2})-(\d{2})\s+בשעה\s+(\d{1,2}):(\d{2})")
_HREF = re.compile(r"/event/(\d+)/(\d+)/")  # relative on the home page, absolute on category pages
_MORE_DATE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")
_MORE_TIME = re.compile(r"(\d{1,2}):(\d{2})")
_PRICE = re.compile(r"(\d+(?:\.\d+)?)")
_EMBED_POINT = re.compile(r"!2d(-?\d+\.\d+)!3d(-?\d+\.\d+)")  # Google Maps embed: !2d<lon>!3d<lat>


class GoShowSettings(SourceSettings):
    site_url: str = "https://www.goshow.co.il"
    listing_path: str = "/"
    show_path: str = "/show/{show_id}/{date_id}/"
    category_path: str = "/event/category/{slug}"
    # The site's categories that map to genres ("misc" maps to none).
    categories: list[str] = Field(
        default_factory=lambda: ["music", "standup", "kids", "lectures", "festivals", "shows"]
    )


class GoShowScraper(BaseScraper[GoShowSettings]):
    name = "goshow"
    settings_model = GoShowSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        cards = parse_listing(await self.get_text(s.site_url + s.listing_path), self.timezone)
        genres: dict[str, set[str]] = {}  # show id -> its site categories
        dates: dict[str, dict[str, Any]] = {}  # every other date seen, by date id
        for slug in s.categories:
            url = s.site_url + s.category_path.format(slug=slug)
            for card in parse_listing(await self.optional(f"category {slug}", self.get_text(url)) or "", self.timezone):
                genres.setdefault(card["show_id"], set()).add(slug)
                dates.setdefault(card["date_id"], card)
        today = datetime.now(zone(self.timezone)).date()
        cards = [c for c in cards if c["start"].date() >= today]
        self.log.debug("listed %d upcoming shows", len(cards))
        shows: dict[str, ScrapedEvent] = {}  # show id -> its read page's event, for its other dates

        def current(event: ScrapedEvent) -> bool:
            return is_current(event.start_at, event.end_at, RetentionSettings(), self.timezone)

        async def read(card: dict[str, Any]) -> ScrapedEvent | None:
            event = card_event(card, s, genres)
            page = await self.get_text(event.url.unicode_string())
            add_page(event, page, s.site_url)
            shows[card["show_id"]] = event
            for more in parse_more_dates(page, self.timezone):
                dates.setdefault(more["date_id"], more)
            return event if current(event) else None

        async for event in self.stream_isolated(cards, read, lambda c: f"date {c['date_id']}"):
            yield event
        listed = {c["date_id"] for c in cards}
        for card in dates.values():
            if card["date_id"] not in listed and current(event := date_event(card, s, genres, shows)):
                yield event


def parse_listing(page_html: str, timezone: str) -> list[dict[str, Any]]:
    """Home page -> one dict per date (first card of each date id): ids, title, venue, city, start."""
    cards: dict[str, dict[str, Any]] = {}
    for node in LexborHTMLParser(page_html).css("li.carousel_row"):
        link = node.css_first("a.closeShowDate")
        href = (link.attributes.get("href") or "") if link else ""
        if not (m := _HREF.search(href)) or m.group(2) in cards:
            continue
        when = _CARD_DATE.search(link.text() if link else "")
        if not when:
            continue
        day, month, year, hour, minute = (int(g) for g in when.groups())
        title = node.css_first(".closeTitle")
        place = node.css_first("a.closeShowTitle")
        venue, _, city = (clean_str(place.text()) or "").rpartition(",") if place else ("", "", "")
        cards[m.group(2)] = {
            "show_id": m.group(1),
            "date_id": m.group(2),
            "title": (clean_str(title.text()) or "").removesuffix("...").strip() if title else "",
            "venue": clean_str(venue) or clean_str(city),
            "city": clean_str(city) if venue else None,
            "start": datetime.combine(date(2000 + year, month, day), time(hour, minute), zone(timezone)),
        }
    return list(cards.values())


def parse_more_dates(page_html: str, timezone: str) -> list[dict[str, Any]]:
    """A date's page -> the producer's other dates (``.moreShows li``), shaped as listing cards + price."""
    found: list[dict[str, Any]] = []
    for node in LexborHTMLParser(page_html).css(".moreShows li"):
        link = node.css_first("a.more-shows")
        day = _MORE_DATE.search(_text(node, ".descDate"))
        clock = _MORE_TIME.search(_text(node, ".descTime"))
        if not link or not (m := _HREF.search(link.attributes.get("href") or "")) or not day or not clock:
            continue
        # "אירוע: <title>" then "מקום: <venue> , <city>" (the commented-out place line is not a node).
        title, _, where = _text(node, "p.descPlace").partition("מקום:")
        venue, _, city = (clean_str(where) or "").rpartition(",")
        found.append(
            {
                "show_id": m.group(1),
                "date_id": m.group(2),
                "title": clean_str(title.replace("אירוע:", "")) or "",
                "venue": clean_str(venue) or clean_str(city),
                "city": clean_str(city) if venue else None,
                "start": datetime.combine(
                    date(int(day.group(3)), int(day.group(2)), int(day.group(1))),
                    time(int(clock.group(1)), int(clock.group(2))),
                    zone(timezone),
                ),
                "price": _price(_text(node, ".descPrice")),
            }
        )
    return found


def date_event(
    card: dict[str, Any], s: GoShowSettings, genres: dict[str, set[str]], shows: dict[str, ScrapedEvent]
) -> ScrapedEvent:
    """A date from a category page or a producer's list: its own date and place, and the details of
    its show's page when that page was read (no request of its own)."""
    event = card_event(card, s, genres)
    if show := shows.get(card["show_id"]):
        event.title, event.description, event.images = show.title, show.description, list(show.images)
        event.tickets, event.min_price, event.max_price = list(show.tickets), show.min_price, show.max_price
        event.extra = {**(show.extra or {}), **(event.extra or {})}
        if show.venue_name == event.venue_name:  # the show's map point, at the same venue only
            event.latitude, event.longitude = show.latitude, show.longitude
    price = card.get("price")
    if price is not None and price not in {t.price for t in event.tickets}:  # this date's own price
        event.tickets, event.min_price, event.max_price = [], price, price
    return event


def _text(node: Any, selector: str) -> str:
    found = node.css_first(selector)
    return found.text() if found else ""


def card_event(card: dict[str, Any], s: GoShowSettings, genres: dict[str, set[str]] | None = None) -> ScrapedEvent:
    url = s.site_url + s.show_path.format(show_id=card["show_id"], date_id=card["date_id"])
    return ScrapedEvent(
        source=GoShowScraper.name,
        source_event_id=card["date_id"],
        url=url,
        title=card["title"],
        start_at=card["start"],
        venue_name=card["venue"],
        city=card["city"],
        address=", ".join(p for p in (card["venue"], card["city"]) if p) or None,
        source_genres=sorted((genres or {}).get(card["show_id"], ())),
        extra={"purchase_url": url},  # GoShow sells the tickets itself: its page is the purchase link
    )


def add_page(event: ScrapedEvent, page_html: str, site_url: str) -> None:
    """The date's page: full title, artwork, text, prices and producer."""
    tree = LexborHTMLParser(page_html)

    def meta(prop: str) -> str | None:
        node = tree.css_first(f'meta[property="{prop}"]')
        return clean_str(node.attributes.get("content")) if node else None

    # The heading, not og:title: the site leaves quotes unescaped there ('ד"ר שי' reads as "ד").
    heading = tree.css_first(".showDescTitle h3")
    if title := (clean_str(heading.text()) if heading else None) or meta("og:title"):
        event.title = title
    if (image := meta("og:image")) and image.startswith("http"):
        event.images = [urljoin(site_url, image)]
    if (desc := tree.css_first(".showDesc")) and (text := html_to_text(desc.html)):
        event.description = text
    tiers = [
        TicketTier(name=name, price=price)
        for row in tree.css("table.orderTable tr")
        if (head := row.css_first("th"))
        and "סוג כרטיס" in head.text()  # not the fees and total rows
        and len(cells := row.css("td")) >= 2
        and (name := clean_str(cells[0].text()))
        and (price := _price(cells[1].text())) is not None
    ]
    web = next((_price(b.text()) for b in tree.css(".showDtlBox") if "מחיר באתר" in b.text()), None)
    prices = sorted({t.price for t in tiers if t.price is not None} | ({web} if web is not None else set()))
    if prices:
        event.min_price, event.max_price = prices[0], prices[-1]
        event.tickets = tiers
    # The venue's map: an embed's point ("!2d<lon>!3d<lat>"). Old "maps?q=<name>" embeds carry no point
    # of the venue (their sll= is a fixed Tel Aviv one) and are left to geocoding. geo.drop_bad_points
    # still clears a point outside the event's city.
    for frame in tree.css("iframe"):
        src = frame.attributes.get("src") or ""
        if "/maps/embed" in src and (m := _EMBED_POINT.search(src)):
            event.longitude, event.latitude = float(m.group(1)), float(m.group(2))
            break
    producer = tree.css_first(".moreEvents a")
    if producer and (name := clean_str(producer.text())):
        event.extra = {**(event.extra or {}), "producer": name.removeprefix("לאירועים נוספים של").strip()}


def _price(text: str | None) -> Any:
    m = _PRICE.search((text or "").replace(",", ""))
    return to_decimal(m.group(1)) if m else None
