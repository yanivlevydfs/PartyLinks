"""The Council for the Preservation of Heritage Sites in Israel (shimur.org, "המועצה לשימור אתרי מורשת בישראל") -
the dated events of the heritage museums and sites it runs and partners with: "שישי מורשת" Fridays, Hanukkah and
heritage-week activities, study days, shows, family days and guided tours at the Ayalon Institute, Minkov citrus
site, the Old Yishuv Court, Kinneret Courtyard, Nahalal police station, Beit HaMeiri, the Circassian heritage centre
and dozens more.

The site is WordPress with its own ``events`` post type (not in wp-json). The archive (``/events/``, one request)
lists every coming item (~90): per card (``#EventsArch a.oneblock``) the page, the first and last day
(``data-sdate`` / ``data-edate``, ``YYYYMMDD``), the title and the category line ("אירועי אתר מכון איילון ברחובות",
"אירועי חנוכה", "שישי מורשת"). Each event page (one request each) gives the site it is at (the header's
``/sites/<slug>/`` link and name), the text with the time ("בשעה 11:00", "בין השעות: 09:00 – 12:30") and the
price ("עלות: 30 ₪"), and the artwork. Each site's page (one request per site, cached for the run) gives its point
(``data-lat`` / ``data-lng``; a site page that fails costs only the point). The town is the one the site's name
ends with ("מוזיאון מכון איילון, רחובות", "מוזיאון בית המאירי בצפת"), else the one at the point (the localities'
list), else ``site_towns``. Not imported: online meetings (Zoom, "מקוון"), guides' training days,
"book a tour" items (bookable on request, months long), and spans longer than ``max_span_days`` (a month's
digest of a museum's programme); an item a few days long (a Hanukkah programme) is one event over its days.
Registration is by phone / the site's own page: the event page is the link. robots.txt allows it; the site
refuses foreign IPs (F5: "Request Rejected" from abroad) and answers Railway direct.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date, datetime, time
from functools import cache
from html import unescape
from urllib.parse import urljoin

import httpx
from pydantic import Field
from selectolax.lexbor import LexborHTMLParser

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, html_to_text, og_image, to_float, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.text_prices import text_tickets
from partylinks.scrapers.ticket4u import town_at

_DAY = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
_CLOCK = re.compile(r"(?<![\d.:])([01]?\d|2[0-3]):([0-5]\d)(?![\d:])")
_RANGE = re.compile(r"(?<![\d.:])([01]?\d|2[0-3]):([0-5]\d)\s*[–-]\s*([01]?\d|2[0-3]):([0-5]\d)(?![\d:])")


class ShimurSettings(SourceSettings):
    site_url: str = "https://shimur.org"
    listing_path: str = "/events/"
    # Longer spans are a month's digest of a museum's programme or a bookable tour, not one event.
    max_span_days: int = Field(12, ge=0)
    # Words of an item that is no public event in a place, in its title or category line.
    skip_words: list[str] = Field(
        default_factory=lambda: ["מקוון", "זום", "זומורשת", "השתלמו", "הזמנת סיור", "מורי דרך", "zoom", "online"]
    )
    # A word in a site's name -> its town, for a site whose name gives none or a wrong one (a point off its place).
    site_towns: dict[str, str] = Field(default_factory=dict)
    # Words of a site's name that are also a village's name ("מורשת" in "מוזיאון מורשת צאן ברזל").
    not_towns: list[str] = Field(default_factory=lambda: ["מורשת", "מרכז", "בית", "אתר", "גן", "חצר", "עין"])
    # A settlement word before a town's name ("קיבוץ צאלים", "כפר הנוער עיינות").
    town_prefixes: list[str] = Field(default_factory=lambda: ["קיבוץ ", "מושב ", "כפר הנוער ", "המושבה "])


@dataclass
class Card:
    """One archive card."""

    url: str
    key: str
    first: date
    last: date
    title: str
    categories: list[str]


class ShimurScraper(BaseScraper[ShimurSettings]):
    name = "shimur"
    settings_model = ShimurSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        cards = parse_listing(await self.get_text(s.site_url + s.listing_path), s.site_url)
        tz = zone(self.timezone)
        keep = [
            c
            for c in cards
            if not is_skipped(c, s)
            and is_current(
                datetime.combine(c.first, time.min, tzinfo=tz),
                datetime.combine(c.last, time(23, 59), tzinfo=tz),
                RetentionSettings(),
                self.timezone,
                time_known=False,
            )
        ]
        keep.sort(key=lambda c: (c.first, c.key))  # nearest first
        self.log.debug("archive lists %d items, %d coming events", len(cards), len(keep))
        sites: dict[str, tuple[float | None, float | None]] = {}

        async def read(card: Card) -> ScrapedEvent | None:
            page = parse_page(await self.get_text(card.url), card.url)
            point: tuple[float | None, float | None] = (None, None)
            if page.site_url:
                if page.site_url not in sites:
                    try:
                        sites[page.site_url] = site_point(await self.get_text(page.site_url))
                    except httpx.HTTPError as exc:  # the point only: the event is still placed by its site's name
                        self.log.info("site page %s: %s", page.site_url, exc)
                        sites[page.site_url] = (None, None)
                point = sites[page.site_url]
            return build_event(card, page, point, s, self.timezone)

        async for event in self.stream_isolated(keep, read, label=lambda c: c.url):
            if event and is_current(
                event.start_at, event.end_at, RetentionSettings(), self.timezone, time_known=event.time_known
            ):
                yield event


def parse_listing(html: str, base: str) -> list[Card]:
    """The archive's cards; a card without a page, a day or a title is skipped."""
    tree = LexborHTMLParser(html)
    cards: list[Card] = []
    seen: set[str] = set()
    for node in tree.css("#EventsArch a.oneblock[href]"):
        href = urljoin(base, (node.attributes.get("href") or "").strip())
        first = _day(node.attributes.get("data-sdate"))
        last = _day(node.attributes.get("data-edate")) or first
        title_node = node.css_first("h3")
        title = clean_str(unescape(title_node.text())) if title_node is not None else None
        if not href.startswith("http") or not first or not last or not title or href in seen:
            continue
        seen.add(href)
        lines = [clean_str(li.text(separator=" ")) or "" for li in node.css("li")]
        categories = [
            c.strip()
            for line in lines
            if line and not re.search(r"\d{1,2}\.\d{1,2}\.\d{4}", line)
            for c in line.split(",")
        ]
        cards.append(
            Card(
                url=href,
                key=href.rstrip("/").rsplit("/", 1)[-1],
                first=first,
                last=last,
                title=title,
                categories=[c for c in categories if c],
            )
        )
    return cards


def is_skipped(card: Card, s: ShimurSettings) -> bool:
    """Online, training, bookable-tour and month-long items."""
    text = f"{card.title} {' '.join(card.categories)}".casefold()
    if any(w.casefold() in text for w in s.skip_words):
        return True
    return (card.last - card.first).days > s.max_span_days


@dataclass
class Page:
    site_name: str | None
    site_url: str | None
    text: str | None
    image: str | None


def parse_page(html: str, url: str) -> Page:
    """An event page: its site (the header's /sites/ link), text and artwork."""
    tree = LexborHTMLParser(html)
    site = None
    for link in tree.css("ul.info a[href]"):
        if "/sites/" in (link.attributes.get("href") or ""):
            site = link
            break
    entry = tree.css_first(".entry")
    text = html_to_text(entry.html) if entry is not None and entry.html else None
    image = og_image(html)
    if not image and entry is not None and (img := entry.css_first("img[src]")) is not None:
        image = (img.attributes.get("src") or "").strip() or None
    return Page(
        site_name=clean_str(unescape(site.text())) if site is not None else None,
        site_url=urljoin(url, site.attributes.get("href") or "") if site is not None else None,
        text=text,
        image=image if image and image.startswith("http") else None,
    )


def site_point(html: str) -> tuple[float | None, float | None]:
    """A site page's point (its map's ``data-lat`` / ``data-lng``)."""
    node = LexborHTMLParser(html).css_first("[data-lat][data-lng]")
    if node is None:
        return None, None
    lat, lng = to_float(node.attributes.get("data-lat")), to_float(node.attributes.get("data-lng"))
    if lat is None or lng is None or not (29 < lat < 34 and 34 < lng < 36):
        return None, None
    return lat, lng


@cache
def _towns() -> dict[str, str]:
    """Every town name (configured cities first, then GeoNames' localities, aliases too) -> its canonical name."""
    from partylinks.config import get_settings  # local: the module stays importable without settings
    from partylinks.places import israeli_places

    names: dict[str, str] = {}
    for town in (*get_settings().enrichment.cities, *israeli_places(0)):
        for name in (town.he, *town.aliases):
            if re.search(r"[א-ת]", name):
                names.setdefault(name, town.he)
    return names


def site_city(name: str, point: tuple[float | None, float | None], s: ShimurSettings) -> str | None:
    """The town of a site: a ``site_towns`` word; the town its name ends with ("..., רחובות"); a town of several
    words its name holds ("מוזיאון העיר תל אביב-יפו ע"ש ..."); a word of its name, last first ("מוזיאון בית
    המאירי בצפת", "משטרת נהלל ההיסטורית"); else the town at its point (some sites' points are off, so the name
    comes first); else the name's tail as written."""
    for word, town in s.site_towns.items():
        if word in name:
            return town
    towns = _towns()
    tail = site_town(name)
    for prefix in s.town_prefixes:
        if tail and tail.startswith(prefix):
            tail = tail[len(prefix) :]
    if tail and tail in towns:
        return towns[tail]
    clean = re.sub(r"[\"'״׳()]", " ", name)
    long_names = [t for t in towns if (" " in t or "-" in t) and len(t) > 4 and t in clean]
    if long_names:
        return towns[max(long_names, key=len)]
    for word in reversed(clean.replace("-", " ").split()):
        for form in (word, word[1:] if word.startswith("ב") else None):
            if form and len(form) > 1 and form in towns and form not in s.not_towns:
                return towns[form]
    return town_at(*point) or tail  # no known town: the name's own tail ("צריף בן גוריון, שדה בוקר")


def site_town(name: str | None) -> str | None:
    """The town a site's name ends with ("מוזיאון מכון איילון, רחובות", "המרכז למורשת הצ'רקסית - כפר כמא")."""
    if not name:
        return None
    parts = re.split(r",\s*|\s+[–-]\s+", name)
    return clean_str(parts[-1]) if len(parts) > 1 else None


def build_event(
    card: Card, page: Page, point: tuple[float | None, float | None], s: ShimurSettings, timezone: str
) -> ScrapedEvent | None:
    """A card, its page and its site's point -> the event; None without a site to place it."""
    if not page.site_name:
        return None
    tz = zone(timezone)
    lat, lng = point
    city = site_city(page.site_name, point, s)
    if not city:
        return None
    text = page.text or ""
    start_clock = end_clock = None
    if clock := _CLOCK.search(text):  # the first clock is the start; a range from it gives the end
        start_clock = time(int(clock.group(1)), int(clock.group(2)))
        if (span := _RANGE.match(text, clock.start())) is not None:
            end_clock = time(int(span.group(3)), int(span.group(4)))
    start = datetime.combine(card.first, start_clock or time.min, tzinfo=tz)
    end = None
    if card.last > card.first:
        end = datetime.combine(card.last, end_clock or time(23, 0), tzinfo=tz)
    elif end_clock and end_clock > (start_clock or time.min):
        end = datetime.combine(card.first, end_clock, tzinfo=tz)
    tickets = text_tickets(text)
    prices = [t.price for t in tickets if t.price is not None]
    declared = [c for c in card.categories if not c.startswith(("אירועי אתר", "אירוע אתר"))]
    return ScrapedEvent(
        source=ShimurScraper.name,
        source_event_id=card.key,
        url=card.url,
        title=card.title,
        description=page.text,
        start_at=start,
        end_at=end,
        time_known=start_clock is not None,
        venue_name=page.site_name,
        address=None if lat is not None else page.site_name,
        city=city,
        latitude=lat,
        longitude=lng,
        source_genres=list(dict.fromkeys(c.lower() for c in declared)),
        organizer="המועצה לשימור אתרי מורשת בישראל",
        images=[page.image] if page.image else [],
        tickets=tickets,
        min_price=min(prices, default=None),
        max_price=max(prices, default=None),
        extra={"purchase_url": card.url},
    )


def _day(value: str | None) -> date | None:
    match = _DAY.match((value or "").strip())
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None
