"""Western Galilee tourism (bgalil.co.il, "תיירות גליל מערבי"): the regional tourism association's
guide. Its "אירועי החודש" page (``/events``) and the "חנוכריסמס בגליל המערבי" section (eight
sub-pages: attractions, lodging, culture, culinary, workshops, content tours, nature tours, shows)
list cards of the businesses' events: the Mi'ilya Christmas tour, Akko's Hanukkah-Christmas in the
Knights' Halls, concerts, workshops, Hanukkah tours.

A card is a link to the item's page (``/<slug>`` or ``index.php?...op=item&cs=<id>``) with the
item id (``data-cs``), a title and a thumbnail. The site has no date field: the date is written in
the title ("17.12.26 - ...", "4/12 - ...", "06-10.12.26", "9-10 לדצמבר") or, when the title has
none, in the page's text ("משבת 05/12/2026 עד שבת 12/12/2026"). Each item's page gives the text,
the address ("כתובת:"), a Google Maps embed (the pin), the booking link ("הזמן עכשיו") and the
pictures. An item that names no date (a restaurant, a zimmer, an open-all-year attraction) is a
listing, not an event, and is skipped. An item with several dates is one event per date (up to
``max_dates``); a longer run is one event over the whole span.

Not read: the business directory sections (hotels, restaurants, wineries, spas, tour guides, trails,
"Tour Galil" packages: no dates), the 2019 sitemap (116 old directory pages), the English mirror
(bgalil.com: the same items by the same ids, titles left empty).
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from functools import cache
from urllib.parse import urljoin

from pydantic import Field
from selectolax.lexbor import LexborHTMLParser

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, html_to_text, zone
from partylinks.places import PlaceIndex, israeli_places
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.text_prices import says_free, text_tickets

_MONTHS = ["ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני", "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר"]
_MONTH_NO = {name: i + 1 for i, name in enumerate(_MONTHS)} | {"מרס": 3}
_DAY_WORD = r"(?:(?:יום\s+)?(?:ראשון|שני|שלישי|רביעי|חמישי|שישי|שבת)\s+)?"
_D = r"(?<![\d/.:])(\d{1,2})"
_DM = _D + r"[./](\d{1,2})(?:[./](\d{4}|\d{2}))?(?![\d:])"
# 05/12/2026 עד שבת 12/12/2026 ; 08/12/-/11/12 ; 15/12-18/12
_FULL_RANGE = re.compile(
    _DM + r"\s*/?\s*(?:-|–|עד)\s*" + _DAY_WORD + r"/?(\d{1,2})[./](\d{1,2})(?:[./](\d{4}|\d{2}))?(?![\d:])"
)
# 06-10.12.26 ; 18/19.12.26 ; 18+19/12/2026
_DAY_RANGE = re.compile(_D + r"\s*[-–+/]\s*(\d{1,2})[./](\d{1,2})(?:[./](\d{4}|\d{2}))?(?![\d:])")
_SINGLE = re.compile(_DM)
# 9-10 לדצמבר ; 4 בדצמבר 2026
_HEB = re.compile(r"(?<!\d)(\d{1,2})(?:\s*[-–]\s*(\d{1,2}))?\s+(?:ל|ב)?(" + "|".join(_MONTH_NO) + r")(?:\s+(\d{4}))?")
_CLOCK_RANGE = re.compile(r"(\d{1,2}):(\d{2})\s*(?:-|–|עד)\s*(\d{1,2}):(\d{2})(?![\d:])")
# A date in the text without its year counts only after a date word: "9.8 בבוקינג" is a rating, "24/7" hours.
_DATE_CUE = re.compile(r"תאריך|מתי|יום|ימי|בין|עד|החל|פתיחה|נעילה|\||:\s*$")
_CUE_WINDOW = 25
# "פתיחה: 17.10.26 נעילה: 16.1.27": an exhibition's run, one event over it.
_RUN = re.compile(r"פתיחה:?\s*(?:ב-?)?" + _DM + r"\s*נעילה:?\s*(?:ב-?)?" + _DM)
_CLOCK = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?![\d:])")
_MAP_PIN = re.compile(r"!2d(-?\d+\.\d+)!3d(-?\d+\.\d+)")
_HEBREW = re.compile(r"[א-ת]")


class BgalilSettings(SourceSettings):
    site_url: str = "https://www.bgalil.co.il"
    # Listing page -> the site's category label for its cards (None: the events page, no label of its own).
    listings: dict[str, str | None] = Field(
        default_factory=lambda: {
            "events": None,
            "Shows-Events": "מופעים",
            "Attractions-Events": "אטרקציות",
            "tarbutvomanotbagalilhmaravi": "תרבות",  # "תרבות ואומנות": its "אומנות" reads as workshops
            "Workshops-in-the-Western-Galilee": "סדנאות",
            "Content-tours-in-the-Western-Galilee": "סיורי תוכן",
            "siyorimbatevabagalilhmaravi": "סיורים בטבע",
            "kolinryiabgalilhamaravi": "קולינריה",
            "B-B": "לינות",
        }
    )
    max_dates: int = 14  # more dates than this: one event over the whole span
    past_days: int = 60  # a date without a year this far back is next year's
    places_min_population: int = 300  # Rosh HaNikra (~600), Mi'ilya, Regba name the place
    organizer: str = "תיירות גליל מערבי"


@dataclass
class Card:
    href: str
    item_id: str
    title: str
    image: str | None = None
    labels: list[str] = field(default_factory=list)


@dataclass
class Page:
    title: str
    text: str
    address: str | None
    latitude: float | None
    longitude: float | None
    booking: str | None
    images: list[str]


class BgalilScraper(BaseScraper[BgalilSettings]):
    name = "bgalil"
    settings_model = BgalilSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        cards: dict[str, Card] = {}
        for path, label in s.listings.items():
            page = await self.optional(f"listing {path}", self.get_text(f"{s.site_url}/{path}"))
            if page is None:
                continue
            for card in parse_listing(page, s.site_url):
                kept = cards.setdefault(card.item_id, card)
                if label and label not in kept.labels:
                    kept.labels.append(label)
        if not cards:
            raise RuntimeError("no listing page answered")
        today = datetime.now(zone(self.timezone)).date()
        # A title with dates, all past: the page is not fetched. Nearest dated titles first.
        todo = []
        for card in cards.values():
            days = find_dates(card.title, today, s.past_days)
            if days and max(days) < today:
                continue
            todo.append((min(days) if days else date.max, card))
        todo.sort(key=lambda pair: pair[0])
        retention = RetentionSettings()

        async def one(card: Card) -> list[ScrapedEvent]:
            html = await self.get_text(urljoin(s.site_url + "/", card.href))
            return parse_item(card, parse_page(html, s.site_url), s, self.timezone, today)

        async for events in self.stream_isolated([c for _, c in todo], one, lambda c: f"item {c.item_id}"):
            for event in events:
                if is_current(event.start_at, event.end_at, retention, self.timezone):
                    yield event


def parse_listing(html: str, site_url: str) -> list[Card]:
    """The cards of a listing page; links off the site (an Instagram post) and section links are left out."""
    tree = LexborHTMLParser(html)
    out = []
    for wrap in tree.css(".catalog-item-wrap"):
        link = wrap.css_first("a.hotels-catalog-items-item")
        href = clean_str(link.attributes.get("href")) if link else None
        if link is None or not href or href.startswith(("http://", "https://")) or "op=category" in href:
            continue
        fav = wrap.css_first("[data-cs]")
        item_id = clean_str(fav.attributes.get("data-cs")) if fav else None
        title_node = link.css_first(".hotels-caption__title")
        title = " ".join((title_node.text() if title_node else "").split())
        if not title:
            continue
        image = None
        if (img := link.css_first(".hotels-catalog-items-img")) and (
            m := re.search(r"url\(([^)]+)\)", img.attributes.get("style") or "")
        ):
            image = urljoin(site_url + "/", m.group(1).strip("'\" "))
        out.append(Card(href=href, item_id=item_id or href, title=title, image=image))
    return out


def parse_page(html: str, site_url: str) -> Page:
    tree = LexborHTMLParser(html)
    h1 = tree.css_first("h1.title")
    describe = tree.css_first(".hotel-contact-describe")
    short = tree.css_first(".hotels-catalog-items__text")
    text = html_to_text(describe.html) if describe and describe.html else None
    if not text and short:
        text = " ".join(short.text().split())
    address = None
    for label in tree.css("p.icon-desc"):
        if label.text(strip=True).startswith("כתובת"):
            value = label.next
            while value is not None and value.tag != "p":
                value = value.next
            address = clean_str(" ".join(value.text().split()).rstrip(".")) if value is not None else None
            break
    lat = lng = None
    if (frame := tree.css_first('iframe[src*="google.com/maps"]')) and (
        pin := _MAP_PIN.search(frame.attributes.get("src") or "")
    ):
        lng, lat = float(pin.group(1)), float(pin.group(2))
    booking_node = tree.css_first(".icon-desc-link a")
    booking = clean_str(booking_node.attributes.get("href")) if booking_node else None
    images: list[str] = []
    og = tree.css_first('meta[property="og:image"]')
    for src in [
        og.attributes.get("content") if og else None,
        *(i.attributes.get("src") for i in (describe.css("img") if describe else [])),
    ]:
        src = clean_str(src)
        if src and not src.startswith("data:"):
            url = urljoin(site_url + "/", src)
            if url not in images:
                images.append(url)
    return Page(
        title=" ".join((h1.text() if h1 else "").split()),
        text=text or "",
        address=address,
        latitude=lat,
        longitude=lng,
        booking=booking if booking and booking.startswith("http") else None,
        images=images,
    )


def _year(day: int, month: int, year: str | None, today: date, past_days: int) -> date | None:
    try:
        if year:
            return date(int(year) + (2000 if len(year) == 2 else 0), month, day)
        guess = date(today.year, month, day)
    except ValueError:
        return None
    if guess < today - timedelta(days=past_days):
        try:
            guess = date(today.year + 1, month, day)
        except ValueError:
            return None
    return guess


def _span(first: date | None, last: date | None) -> list[date]:
    if first is None or last is None or last < first or (last - first).days > 120:
        return [d for d in (first, last) if d]
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def find_dates(text: str, today: date, past_days: int = 60, *, cued: bool = False) -> list[date]:
    """Every date ``text`` names, ranges spelled out day by day, sorted; a date without a year is the
    nearest one not more than ``past_days`` behind ``today``. ``cued`` (a page's text, not a title): a
    lone day.month without a year counts only after a date word."""
    found: set[date] = set()
    taken: list[tuple[int, int]] = []

    def free(m: re.Match[str]) -> bool:
        return not any(a < m.end() and m.start() < b for a, b in taken)

    for m in _FULL_RANGE.finditer(text):
        d1, m1, y1, d2, m2, y2 = m.groups()
        last = _year(int(d2), int(m2), y2, today, past_days)
        # "15/12-18/12" and "28/12/2026 - 02/01/2027": the first date's year from the last one's.
        first = _year(int(d1), int(m1), y1 or (str(last.year) if last else None), today, past_days)
        if first and last and first > last and not y1:
            first = _year(int(d1), int(m1), str(last.year - 1), today, past_days)
        found.update(_span(first, last))
        taken.append(m.span())
    for m in _DAY_RANGE.finditer(text):
        if not free(m):
            continue
        d1, d2, month, year = m.groups()
        if int(d1) >= int(d2):  # "4/12" read as a range of days is no range
            continue
        found.update(
            _span(
                _year(int(d1), int(month), year, today, past_days), _year(int(d2), int(month), year, today, past_days)
            )
        )
        taken.append(m.span())
    for m in _SINGLE.finditer(text):
        if cued and not m.group(3) and not _DATE_CUE.search(text[max(0, m.start() - _CUE_WINDOW) : m.start()]):
            continue
        if free(m) and (day := _year(int(m.group(1)), int(m.group(2)), m.group(3), today, past_days)):
            found.add(day)
            taken.append(m.span())
    for m in _HEB.finditer(text):
        if not free(m):
            continue
        d1, d2, month, year = m.groups()
        first = _year(int(d1), _MONTH_NO[month], year, today, past_days)
        found.update(
            _span(first, _year(int(d2), _MONTH_NO[month], year, today, past_days)) if d2 else [first] if first else []
        )
    return sorted(found)


def _clock(text: str) -> tuple[time | None, time | None]:
    """The first time of day the text names, and its end when written as a range ("17:00 עד 19:00")."""
    m = _CLOCK.search(text)
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None, None
    start = time(int(m.group(1)), int(m.group(2)))
    if (r := _CLOCK_RANGE.match(text, m.start())) and int(r.group(3)) < 24 and int(r.group(4)) < 60:
        return start, time(int(r.group(3)), int(r.group(4)))
    return start, None


def exhibition_runs(text: str, today: date, past_days: int = 60) -> list[tuple[date, date]]:
    """The runs an exhibition's text gives ("פתיחה: 17.10.26 נעילה: 16.1.27"), opening to closing."""
    runs = []
    for m in _RUN.finditer(text):
        d1, m1, y1, d2, m2, y2 = m.groups()
        last = _year(int(d2), int(m2), y2, today, past_days)
        first = _year(int(d1), int(m1), y1 or (str(last.year) if last else None), today, 366)
        if first and last and first <= last:
            runs.append((first, last))
    return runs


@cache
def _localities(min_population: int) -> PlaceIndex:
    return PlaceIndex(list(israeli_places(min_population)))


@cache
def _centres(min_population: int) -> dict[str, tuple[float, float, float]]:
    """Hebrew name -> (lat, lng, radius in metres) of each locality (the bigger one on a name clash)."""
    out: dict[str, tuple[float, float, float]] = {}
    for place in israeli_places(min_population):
        if place.center is not None:
            out.setdefault(place.he, (place.center[0], place.center[1], place.radius_m))
    return out


def _metres(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    return (((lat1 - lat2) * 111_000) ** 2 + ((lng1 - lng2) * 93_000) ** 2) ** 0.5  # near enough at 33°N


def _city(page: Page, title: str, min_population: int) -> str | None:
    """The locality the address names, else the title, else the text, if it lies near the map's pin
    ("רח׳ מירון 1, כפר ורדים" is in Kfar Vradim, not Meron); else the town whose area holds the pin."""
    index, centres = _localities(min_population), _centres(min_population)
    pin = (page.latitude, page.longitude) if page.latitude is not None and page.longitude is not None else None

    def near(name: str) -> bool:
        if pin is None or name not in centres:
            return True
        lat, lng, radius = centres[name]
        return _metres(lat, lng, pin[0], pin[1]) <= radius * 2 + 3000

    if page.address and index.is_place(page.address) and _HEBREW.search(page.address):
        return page.address  # the site's own spelling ("פקיעין", "רגבה")
    for text in (page.address, title, page.text[:400]):
        for _, name in index.find(text):
            if _HEBREW.search(name) and near(name):
                return name
    if pin is not None:
        best = min(
            (
                (_metres(lat, lng, pin[0], pin[1]), name)
                for name, (lat, lng, radius) in centres.items()
                if _metres(lat, lng, pin[0], pin[1]) <= radius * 2
            ),
            default=None,
        )
        if best:
            return best[1]
    return None


def parse_item(card: Card, page: Page, s: BgalilSettings, timezone: str, today: date) -> list[ScrapedEvent]:
    """The events of one item: a date from its title, else from its text; none when it names no date."""
    title = page.title or card.title
    days = find_dates(title, today, s.past_days)
    runs = [] if days else exhibition_runs(page.text, today, s.past_days)
    if not days and not runs:
        days = find_dates(page.text, today, s.past_days, cued=True)
    if not days and not runs:
        return []
    start_clock, end_clock = _clock(title + " " + page.text)
    tz = zone(timezone)
    city = _city(page, title, s.places_min_population)
    tiers = text_tickets(page.text.replace("התעריף", "המחיר"))  # "התעריף 100 ש״ח לאדם": a price word too
    url = urljoin(s.site_url + "/", card.href)
    images = page.images or ([card.image] if card.image else [])
    spans = runs or ([(d, d) for d in days] if len(days) <= s.max_dates else [(days[0], days[-1])])
    events = []
    for first, last in spans:
        start = datetime.combine(first, start_clock or time(0), tz)
        end = None
        if end_clock is not None:
            end = datetime.combine(last, end_clock, tz)
            if end <= start:
                end += timedelta(days=1)
        elif last > first:
            end = datetime.combine(last, time(23, 59), tz)
        events.append(
            ScrapedEvent(
                source="bgalil",
                source_event_id=f"{card.item_id}-{first.isoformat()}",
                url=url,
                title=title,
                description=page.text or None,
                start_at=start,
                end_at=end,
                time_known=start_clock is not None,
                venue_name=page.address,
                address=page.address,
                city=city,
                latitude=page.latitude,
                longitude=page.longitude,
                source_genres=[label.lower() for label in card.labels],
                organizer=s.organizer,
                images=images,
                tickets=tiers,
                has_free_entry=says_free(page.text),
                extra={"purchase_url": page.booking} if page.booking else {},
            )
        )
    return events
