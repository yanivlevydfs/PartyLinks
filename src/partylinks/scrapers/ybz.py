"""Yad Izhak Ben-Zvi (ybz.org.il, "יד יצחק בן־צבי"), Rehavia, Jerusalem - the institute's lectures, book evenings,
conferences, study days and culture evenings in its auditorium and the President's Hut, and its guided tours in
Jerusalem and around the country.

The site is WordPress + WooCommerce: every dated item is a ``product``. The calendar page (``listing_path``, one
request) holds every coming item, months ahead (~160), soonest first: per item (``.search-event-item``) the product
id (``data-id``), the day (``data-date``, ``YYYYMMDD``), the type tag (``.tag-name``: הרצאה, ערב ספר, סיור, אירוע,
כנס, יום עיון), the title, the place line (the green-marker meta: "יד יצחק בן־צבי", "צריף הנשיא", "זום"), the
hours ("18:00 - 17:00": end, then start) and the product page. Online lectures (the place or title says Zoom,
``online_words``) are not imported. Each product page (one request each) adds the artwork (``og:image``), the
text, the prices (``.price-item``) and, for a tour, its town (``.tour-location``) and meeting point ("נקודת
מפגש: ...", ``meeting_word``). An item at the institute (``own_place_words``) takes the venue, address and point
from the config; a tour meets at its meeting point in its town (geocoded). A coach tour that leaves from a town
("יציאה מירושלים", or a meeting point of "כל הארץ") has no meeting place to pin: its venue says where it leaves
from and only the town is set. The product page is the ticket link (the site's own shop / registration). The type
tag is the category; ``labels`` maps each to the words our genres read. robots.txt allows the site but /wp-admin/
and the shop's log folders.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from html import unescape
from urllib.parse import urljoin

from pydantic import Field
from selectolax.lexbor import LexborHTMLParser

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import clean_str, html_to_text, og_image, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_DAY = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
_CLOCK = re.compile(r"(?<![\d.:])([01]?\d|2[0-3]):([0-5]\d)(?![\d:])")
_PRICE = re.compile(r"(\d[\d,]*(?:\.\d+)?)")


class YbzSettings(SourceSettings):
    site_url: str = "https://ybz.org.il"
    # The calendar ("יומן אירועים", /תרבות/לוח-אירועים/): every coming item on one page.
    listing_path: str = "/%d7%aa%d7%a8%d7%91%d7%95%d7%aa/%d7%9c%d7%95%d7%97-%d7%90%d7%99%d7%a8%d7%95%d7%a2%d7%99%d7%9d/"
    # An online item's place line or title.
    online_words: list[str] = Field(default_factory=lambda: ["זום", "zoom", "מקוונ", "online", "וובינר"])
    # A place line naming the institute (its auditorium, the President's Hut on its grounds).
    own_place_words: list[str] = Field(default_factory=lambda: ["בן־צבי", "בן-צבי", "בן צבי", "צריף הנשיא"])
    # The text's meeting point line, and the words of a coach tour that leaves from a town (no place to pin).
    meeting_word: str = "נקודת מפגש:"
    departure_words: list[str] = Field(default_factory=lambda: ["יציאה מ", "כל הארץ"])
    # A town / meeting line of an overnight campus's later day (from its hotel): not a day of its own.
    package_words: list[str] = Field(default_factory=lambda: ["מלון"])
    # Sold out: the product page's stock line (``.last-places``).
    sold_out_words: list[str] = Field(default_factory=lambda: ["אזל המלאי"])
    # A type tag -> the words declared beside it (our genres read them).
    labels: dict[str, list[str]] = Field(default_factory=dict)
    venue_name: str
    address: str
    city: str
    latitude: float | None = None
    longitude: float | None = None


@dataclass
class Item:
    """One calendar row."""

    product_id: str
    url: str
    day: date
    tag: str | None
    title: str
    place: str | None
    start: time | None
    end: time | None
    guide: str | None


class YbzScraper(BaseScraper[YbzSettings]):
    name = "ybz"
    settings_model = YbzSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        items = parse_listing(await self.get_text(s.site_url + s.listing_path), s.site_url)
        tz = zone(self.timezone)
        keep = [
            i
            for i in items
            if not is_online(i, s)
            and is_current(
                datetime.combine(i.day, i.start or time.min, tzinfo=tz),
                datetime.combine(i.day, i.end, tzinfo=tz) if i.end and i.start and i.end > i.start else None,
                RetentionSettings(),
                self.timezone,
                time_known=i.start is not None,
            )
        ]
        self.log.debug("calendar lists %d items, %d coming and in a place", len(items), len(keep))

        async def read(item: Item) -> ScrapedEvent | None:
            return parse_event(item, await self.get_text(item.url), s, self.timezone)

        async for event in self.stream_isolated(
            keep, read, label=lambda i: i.url
        ):  # the calendar's order: soonest first
            if event and is_current(
                event.start_at, event.end_at, RetentionSettings(), self.timezone, time_known=event.time_known
            ):
                yield event


def parse_listing(html: str, base: str) -> list[Item]:
    """The calendar's rows, in its order (soonest first); a row without an id, a day, a title or a page is skipped."""
    tree = LexborHTMLParser(html)
    items: list[Item] = []
    seen: set[str] = set()
    for node in tree.css(".search-event-item"):
        product_id = (node.attributes.get("data-id") or "").strip()
        day = _DAY.match((node.attributes.get("data-date") or "").strip())
        title = clean_str(unescape(_text(node, ".event-title")))
        link = node.css_first(".button-wrap a[href]") or node.css_first("a[href]")
        href = (link.attributes.get("href") or "").strip() if link else ""
        if not product_id or not day or not title or not href or product_id in seen:
            continue
        seen.add(product_id)
        place = guide = None
        start = end = None
        for meta in node.css(".meta-item"):
            icon = meta.css_first("noscript img") or meta.css_first("img")
            kind = _icon(icon)
            value = clean_str(unescape(_text(meta, ".text")))
            if not value:
                continue
            if "map-marker" in kind:
                place = value
            elif "time" in kind:
                start, end = hours(value)
            elif "guide" in kind:
                guide = value
        items.append(
            Item(
                product_id=product_id,
                url=urljoin(base, href),
                day=date(int(day.group(1)), int(day.group(2)), int(day.group(3))),
                tag=clean_str(_text(node, ".tag-name")),
                title=title,
                place=place,
                start=start,
                end=end,
                guide=guide,
            )
        )
    return items


def hours(text: str) -> tuple[time | None, time | None]:
    """The meta's hours -> (start, end). The page writes "end - start" ("18:00 - 17:00"); one clock is the start."""
    clocks = [time(int(h), int(m)) for h, m in _CLOCK.findall(text)]
    if not clocks:
        return None, None
    if len(clocks) == 1:
        return clocks[0], None
    first, second = clocks[0], clocks[1]
    return (second, first) if second <= first else (first, second)


def is_online(item: Item, s: YbzSettings) -> bool:
    """A Zoom lecture: its place line or title says so."""
    words = [w.casefold() for w in s.online_words]
    return any(w in f"{item.place or ''} {item.title}".casefold() for w in words)


def parse_event(item: Item, html: str, s: YbzSettings, timezone: str) -> ScrapedEvent | None:
    """A calendar row and its product page -> the event; None when the page says it is online."""
    tree = LexborHTMLParser(html)
    tz = zone(timezone)
    start = datetime.combine(item.day, item.start or time.min, tzinfo=tz)
    end = None
    if item.start and item.end:
        end = datetime.combine(item.day, item.end, tzinfo=tz)
        if end <= start:
            end += timedelta(days=1)
    town = clean_str(_text(tree, ".tour-location .text"))
    body = tree.css_first(".product-the-content") or tree.css_first(".about-the-tour .wp-the-content")
    text = html_to_text(body.html) if body is not None and body.html else None
    subtitle = clean_str(_text(tree, ".tour-description"))
    description = "\n\n".join(t for t in (subtitle, text) if t) or None
    meeting = meeting_point(text or "", s.meeting_word)
    words = [w.casefold() for w in s.online_words]
    if any(w in (meeting or "").casefold() for w in words):
        return None
    if any(w in f"{town or ''} {meeting or ''}" for w in s.package_words):
        return None  # a campus's second day, from its hotel: part of an overnight package, not a day of its own
    own = item.place is not None and any(w in item.place for w in s.own_place_words)
    if own or (not town and not meeting):
        venue, address, city, lat, lng = s.venue_name, s.address, s.city, s.latitude, s.longitude
    else:
        city = normalize_town(town) or s.city
        departs = any(w in f"{town or ''} {meeting or ''}" for w in s.departure_words)
        if departs or not meeting or meeting.strip(" .") == city:
            venue, address = f"יציאה מ{city}", None  # a coach tour: the town it leaves from, no place to pin
        else:
            venue, address = meeting, street_part(meeting)
        lat = lng = None
    tickets = price_tiers(tree)
    stock = _text(tree, ".last-places")  # "אזל המלאי" / "נותרו במלאי 5 כרטיסים"
    sold_out = any(w in stock for w in s.sold_out_words)
    prices = [t.price for t in tickets if t.price is not None]
    declared = [item.tag, *s.labels.get(item.tag or "", [])] if item.tag else []
    image = og_image(html)
    return ScrapedEvent(
        source=YbzScraper.name,
        source_event_id=item.product_id,
        url=item.url,
        title=item.title,
        description=description,
        start_at=start,
        end_at=end,
        time_known=item.start is not None,
        venue_name=venue,
        address=address,
        city=city,
        latitude=lat,
        longitude=lng,
        source_genres=list(dict.fromkeys(g.lower() for g in declared if g)),
        artists=[],
        organizer=s.venue_name,
        images=[image] if image and image.startswith("http") else [],
        tickets=tickets,
        min_price=min(prices, default=None),
        max_price=max(prices, default=None),
        is_sold_out=bool(sold_out and tickets),
        extra={"purchase_url": item.url, **({"guide": item.guide} if item.guide else {})},
    )


def meeting_point(text: str, word: str) -> str | None:
    """The text's "נקודת מפגש: ..." line, without the label."""
    for line in text.splitlines():
        if word in line:
            return clean_str(line.split(word, 1)[1].strip(" :–-"))
    return None


def street_part(meeting: str) -> str:
    """The meeting point's street address: the part after its last dash ("מאפיית ברמן – אגריפס 42" -> "אגריפס 42")
    when that part has a house number, else the whole line."""
    parts = [p.strip(" .") for p in re.split(r"\s[–-]\s", meeting) if p.strip(" .")]
    tail = parts[-1] if parts else meeting
    return tail if len(parts) > 1 and re.search(r"\d", tail) else meeting


def normalize_town(town: str | None) -> str | None:
    """The tour's town line as our cities write it ("תל־אביב-יפו" -> "תל אביב-יפו", "יציאה מירושלים" -> ירושלים)."""
    if not town:
        return None
    town = re.sub(r"^יציאה\s*מ", "", town.strip()).replace("־", " ").strip()
    return clean_str(town)


def price_tiers(tree: LexborHTMLParser) -> list[TicketTier]:
    """The product's price rows (``.price-item``: a name and "₪120.00")."""
    tiers: list[TicketTier] = []
    for row in tree.css(".price-item"):
        name = clean_str(_text(row, ".item-name")) or "כרטיס"
        match = _PRICE.search(_text(row, ".item-price").replace("₪", " "))
        if not match:
            continue
        try:
            price = Decimal(match.group(1).replace(",", ""))
        except InvalidOperation:
            continue
        tiers.append(TicketTier(name=name, price=price, is_free=price == 0))
    return tiers


def _icon(node: object) -> str:
    if node is None:
        return ""
    attrs = getattr(node, "attributes", {}) or {}
    return str(attrs.get("data-lazy-src") or attrs.get("src") or "")


def _text(node: object, selector: str) -> str:
    found = node.css_first(selector) if node is not None else None  # type: ignore[attr-defined]
    return found.text(separator=" ") if found is not None else ""
