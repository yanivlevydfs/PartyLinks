"""Municipal and council event calendars on the A.S. Bina CMS (binaa.co.il): ~90 cities, councils,
matnasim and culture companies (rosh-haayin.muni.il, eshkol.info, herzliya-matnasim.org.il, ...).

Every site has ``/events/`` and, on most, the FullCalendar feed ``/events/json/?start=&end=``:
one row per occurrence with id, title, ISO start/end, url, pic, cancelled and, on newer installs,
locationName and categoryName (older ones give a category id only; the listing's
``select#category`` names it). Some installs ignore the date range and return every event since
2016; past ones are filtered here. A recurring event has a row per date and is kept once, at its
next date, so its id rolls forward instead of flooding the calendar. Where the feed is missing
(500 / HTML: Herzliya, Lod, Givat Shmuel, Eshkol, Ganei Tikva, ...) the listing page is parsed
instead: whatever the layout (``a.event-promo`` grid, ``.events-list``, council ``a.event`` cards,
month calendar), each link to ``./events/<id>/`` is a card, read for title, date, time, place and
tags. Each event's page ``/events/<id>/`` adds the description, the full-size image, the place
when the listing lacks it and, when the site sells tickets itself, a tickets row
("40 ₪ • אירוע בתשלום"). Events linking off the site (a Smarticket hall, Tickchak, a news item) are
skipped: their seller has its own source. Undated events ("ללא תאריך", "עד ...") and cancelled ones
are skipped. Mostly no robots.txt (404); where there is one it allows ``/events/``. A bare user
agent is redirected to the host's abuse page (abuse.spd.co.il).
"""

from __future__ import annotations

import html
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from functools import cache
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field
from selectolax.lexbor import LexborHTMLParser, LexborNode

from partylinks.models import ScrapedEvent, TicketTier, TierStatus
from partylinks.parsing import clean_str, html_to_text, zone
from partylinks.places import PlaceIndex, israeli_places
from partylinks.scrapers.base import BaseScraper, SourceSettings

_DATE = re.compile(r"(\d{1,2})[./](\d{1,2})[./](\d{4})")
_MONTHS = ["ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני", "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר"]
_HEB_DATE = re.compile(r"(\d{1,2})\s+ב?(" + "|".join([*_MONTHS, "מרס"]) + r")(?:\s+(\d{4}))?")
_CLOCK = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?![\d:])")
_PRICE = re.compile(r"(\d+(?:\.\d+)?)\s*₪")
_LEFT = re.compile(r"נותרו רק (\d+)")
_EVENT_ID = re.compile(r"/events/(\d+)/?$")
_HEBREW = re.compile(r"[א-ת]")
_BG_URL = re.compile(r"url\(['\"]?([^'\")]+)")
FEED_DAYS = 365  # feed window each way: a season that began last autumn still runs


class MuniSite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str  # https://www.rosh-haayin.muni.il  (the listing is <url>/events/, the feed <url>/events/json/)
    city: str  # Hebrew locality; for a regional council its name, used when the place names no locality
    regional: bool = False  # regional council: the event's city is the locality its place names
    organizer: str | None = None  # default "עיריית <city>"
    feed: bool = True  # false where /events/json/ is missing (500 / HTML): read the listing page only
    category: str = "קהילה"  # for events the site files under no category


class MuniEventsSettings(SourceSettings):
    sites: list[MuniSite] = Field(min_length=1)


@dataclass
class Card:
    """One listed event, before its page is read."""

    url: str
    event_id: str
    title: str
    start: datetime | None
    end: datetime | None
    place: str | None = None
    image: str | None = None
    tags: list[str] = field(default_factory=list)
    ribbon: str | None = None
    last_day: date | None = None  # the last day it runs (a season's end), for leaving out past ones
    undated: bool = False  # listed as "ללא תאריך" / "עד ...": its page's date is not a start either


class MuniEventsScraper(BaseScraper[MuniEventsSettings]):
    name = "munievents"
    settings_model = MuniEventsSettings

    def is_blocked(self, response: httpx.Response) -> bool:
        return response.url.host.startswith("abuse.")

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        tz = zone(self.timezone)
        today = datetime.now(tz).date()
        window = f"start={today - timedelta(FEED_DAYS)}&end={today + timedelta(FEED_DAYS)}"
        for site in self.settings.sites:
            feed_url = f"{site.url}/events/json/?{window}"
            feed = await self.optional(f"{site.url} feed", self.get_json(feed_url, expect=list)) if site.feed else None
            page = await self.optional(f"{site.url} listing", self.get_text(site.url + "/events/")) or ""
            listed = parse_listing(page, site.url, self.timezone, today)
            if feed:
                cards = parse_feed(feed, site.url, parse_categories(page), self.timezone, today)
                tags = {c.url: c.tags for c in listed}  # the grid's #tags add to the feed's category
                for card in cards:
                    card.tags = list(dict.fromkeys([*card.tags, *tags.get(card.url, [])]))
            else:
                cards = listed
            self.log.info("%s: %d upcoming events (%s)", site.url, len(cards), "feed" if feed else "listing")

            async def fetch(card: Card, site: MuniSite = site) -> ScrapedEvent | None:
                listed_only = build_event(card, None, site, self.timezone)
                if listed_only is not None and not self.wanted(listed_only):
                    return listed_only
                detail = await self.optional(card.url, self.get_text(card.url))
                return build_event(card, detail, site, self.timezone)

            async for event in self.stream_isolated(cards, fetch, label=lambda c: c.url):
                yield event


def _text(node: LexborNode | None) -> str:
    return " ".join((node.text() if node else "").split())


def _unescape(value: Any) -> str | None:
    """Feed text is escaped once or twice ("מתנ&amp;quot;ס")."""
    text = str(value or "")
    while (plain := html.unescape(text)) != text:
        text = plain
    return clean_str(text)


def _local_event(href: str | None, base: str) -> tuple[str, str] | None:
    """(absolute url, id) of a link to one of the site's own event pages."""
    url = urljoin(base + "/", href or "")
    found = _EVENT_ID.search(urlsplit(url).path)
    if not found or urlsplit(url).netloc != urlsplit(base).netloc:
        return None
    return url, found.group(1)


def _iso(value: Any, tz: Any) -> datetime | None:
    text = str(value or "")
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        try:
            moment = datetime.fromisoformat(text[:10])  # "2026-11-14T2026-11-14+00:00": its day
        except ValueError:
            return None
    # Older installs stamp local times "+00:00"; no Israeli council means UTC.
    return moment.replace(tzinfo=tz) if not moment.utcoffset() else moment


def parse_categories(page_html: str) -> dict[str, str]:
    """Category id -> name, from the listing's category filter."""
    if not page_html:
        return {}
    options = LexborHTMLParser(page_html).css("select#category option")
    return {o.attributes.get("value") or "": _text(o) for o in options if (o.attributes.get("value") or "0") != "0"}


def parse_feed(rows: list[Any], base: str, categories: dict[str, str], timezone: str, today: date) -> list[Card]:
    """The feed's own upcoming events, each at its next date; links off the site are left out."""
    tz = zone(timezone)
    cards: dict[str, Card] = {}
    dated = [(start, r) for r in rows if isinstance(r, dict) and (start := _iso(r.get("start"), tz))]
    for start, row in sorted(dated, key=lambda x: x[0]):
        link = _local_event(row.get("url"), base)
        title = _unescape(row.get("title"))
        if not link or not title or link[0] in cards or str(row.get("cancelled") or "0") not in ("0", ""):
            continue
        end = _iso(row.get("end"), tz)
        last_day = max(start, end or start).date()
        if last_day < today:
            continue
        if end and (end <= start or end.time() in (time(), time(23, 59, 59))):  # an end day, not an hour
            end = None
        category = _unescape(row.get("categoryName")) or categories.get(str(row.get("category")))
        pic = clean_str(row.get("pic"))
        cards[link[0]] = Card(
            url=link[0],
            event_id=str(row.get("id") or link[1]),
            title=title,
            start=start,
            end=end,
            place=_unescape(row.get("locationName")),
            image=urljoin(base + "/", pic) if pic and "no-pic" not in pic else None,
            tags=[category] if category else [],
            last_day=last_day,
        )
    return list(cards.values())


def _card_node(a: LexborNode, href: str) -> LexborNode:
    """The largest element around the link that holds no other link: the event's card."""
    node = a
    while (parent := node.parent) is not None and parent.tag not in ("body", "html"):
        if any((x.attributes.get("href") or "") != href for x in parent.css("a[href]")):
            break
        node = parent
    return node


def _days(text: str, today: date) -> list[date]:
    """Every date in the text: 01.11.2026, 01/11/2026 or "6 באוקטובר 2026" (no year: the next one)."""
    found: list[tuple[int, date]] = []
    for m in _DATE.finditer(text):
        try:
            found.append((m.start(), date(int(m.group(3)), int(m.group(2)), int(m.group(1)))))
        except ValueError:
            continue
    for m in _HEB_DATE.finditer(text):
        month = _MONTHS.index(m.group(2)) + 1 if m.group(2) in _MONTHS else 3
        year = int(m.group(3) or today.year)
        try:
            day = date(year, month, int(m.group(1)))
        except ValueError:
            continue
        if not m.group(3) and day < today - timedelta(60):  # "5 ינואר" read in October
            day = day.replace(year=year + 1)
        found.append((m.start(), day))
    return [d for _, d in sorted(found, key=lambda x: x[0])]


def parse_listing(page_html: str, base: str, timezone: str, today: date | None = None) -> list[Card]:
    """The listing's own cards, whatever the layout; links off the site are left out."""
    if not page_html:
        return []
    tz = zone(timezone)
    today = today or datetime.now(tz).date()
    cards: dict[str, Card] = {}
    for a in LexborHTMLParser(page_html).css("a[href]"):
        href = a.attributes.get("href") or ""
        link = _local_event(href, base)
        if not link or link[0] in cards:  # a recurring event: its first card is the next date
            continue
        card = _card_node(a, href)
        heading = card.css_first(".name, h2, h3, h4, .title")
        title = clean_str(_text(heading) or _text(a))
        if not title:
            continue
        date_nodes = card.css(".date, .date_time, .icon, .day")
        date_text = " ".join(
            [_text(n) for n in date_nodes] + [n.attributes.get("title") or "" for n in date_nodes]
        ) or _text(card)
        undated = date_text.startswith(("עד", "ללא תאריך"))
        days = [] if undated else _days(date_text, today)
        clock_text = " ".join(
            _text(n.parent if "fa" in (n.attributes.get("class") or "") else n)
            for n in card.css(".time, .fa-clock, .fa-clock-o")
        )
        clocks = [time(int(h) % 24, int(m)) for h, m in _CLOCK.findall(clock_text)]
        if not clocks:
            clocks = [time(int(h) % 24, int(m)) for h, m in _CLOCK.findall(date_text)][:1]
        start = datetime.combine(days[0], clocks[0] if clocks else time(), tz) if days else None
        end = datetime.combine(days[0], clocks[1], tz) if days and len(clocks) > 1 else None
        if days and max(days) < today:
            continue
        place = card.css_first(".place, .location")
        marker = card.css_first(".fa-map-marker, .fa-location-arrow, .fa-location-dot")
        img = card.css_first(".pic img, img")
        tag_nodes = card.css(".tags .badge, .category, .info > div:not([class]), a.event > div:not([class])")
        tags = [t.lstrip("#") for t in (_text(n) for n in tag_nodes) if t and len(t) <= 40 and not _days(t, today)]
        cards[link[0]] = Card(
            url=link[0],
            event_id=link[1],
            title=title,
            start=start,
            end=end,
            place=clean_str(_text(place) or (_text(marker.parent) if marker and marker.parent else "")),
            image=urljoin(base + "/", img.attributes["src"] or "") if img and img.attributes.get("src") else None,
            tags=list(dict.fromkeys(tags)),
            ribbon=clean_str(_text(card.css_first(".ribbon"))),
            last_day=max(days) if days else None,
            undated=undated,
        )
    return list(cards.values())


def _split_place(place: str | None) -> tuple[str | None, str | None]:
    """Venue and address of "מרכז פיס תורני - רח' המתמיד 23"; several places stay whole."""
    if not place or "|" in place:
        return place, None
    venue, *address = re.split(r"\s+-\s+|,\s+", place, maxsplit=1)
    return clean_str(venue), clean_str(address[0]) if address else None


@cache
def _localities() -> PlaceIndex:
    return PlaceIndex(list(israeli_places(500)))


def _city(site: MuniSite, place: str | None) -> str:
    """The site's city; for a regional council the locality its place names ("בית האומנויות - נאות סמדר")."""
    hits = [name for _, name in _localities().find(place) if _HEBREW.search(name)] if site.regional else []
    return hits[0] if hits else site.city  # GeoNames' Hebrew name for some towns is a transliteration


def _detail(page: LexborHTMLParser, base: str, tz: Any) -> dict[str, Any]:
    """What an event page adds: description, image, place, tickets, and the date when unlisted."""
    content = page.css_first("#event-page-content .rich-content") or page.css_first(".rich-content")
    paragraphs = [] if content else (page.css_first(".page-content-body") or page.css_first("main") or page).css("p")
    body = content.html if content else "".join(p.html or "" for p in paragraphs if len(_text(p)) > 20)
    pic = page.css_first(".event-item-pic img")
    bg = page.css_first(".event-item-pic .pic")
    og = page.css_first('meta[property="og:image"]')
    image = (
        (pic.attributes.get("src") if pic else None)
        or (m.group(1) if bg and (m := _BG_URL.search(bg.attributes.get("style") or "")) else None)
        or (og.attributes.get("content") if og else None)
    )
    gcal = page.css_first('a[href*="calendar.google.com"]')
    gcal_place = parse_qs(urlsplit(gcal.attributes.get("href") or "").query).get("location", [""])[0] if gcal else ""
    place = (
        _text(page.css_first(".event-item-row.where .event-item-value"))
        or _text(page.css_first(".atc_location"))
        or gcal_place
    )
    start = _iso(_text(page.css_first(".atc_date_start")).replace(" ", "T"), tz)
    end = _iso(_text(page.css_first(".atc_date_end")).replace(" ", "T"), tz)
    tickets = page.css_first(".event-item-row.tickets")
    return {
        "description": html_to_text(body),
        "image": urljoin(base + "/", image) if image and "no-pic" not in image else None,
        "place": clean_str(place),
        "start": start,
        "end": end if start and end and end > start else None,
        "price_text": _text(tickets.css_first(".event-item-value")) if tickets else None,
        "sold_out": bool(tickets) and "הכרטיסים אזלו" in _text(tickets),
    }


def build_event(card: Card, detail_html: str | None, site: MuniSite, timezone: str) -> ScrapedEvent | None:
    """The card plus what its page adds; None when it has no start date or is cancelled."""
    tz = zone(timezone)
    extra = _detail(LexborHTMLParser(detail_html), site.url, tz) if detail_html else {}
    start, end = card.start, card.end
    page_start = extra.get("start")
    if page_start is not None and page_start.second:  # the page's clock ("now"), not a date it was given
        page_start = None
    page_end = extra.get("end") if page_start and page_start.time() != time() else None  # no hour: no end
    if start is None and not card.undated:
        start, end = page_start, page_end
    elif start and page_start and start.time() == time() and page_start.date() == start.date():
        start, end = page_start, end or page_end  # listed by day only: the page has the hour
    if start is None or "בוטל" in (card.ribbon or ""):
        return None
    sold_out = card.ribbon == "אזל המלאי" or bool(extra.get("sold_out"))
    price_text = extra.get("price_text")
    prices = [Decimal(p) for p in _PRICE.findall(price_text or "")]
    left = _LEFT.search(card.ribbon or "")
    status = TierStatus.SOLD_OUT if sold_out else TierStatus.ON_SALE
    tiers = [
        TicketTier(name="כרטיס", price=p, status=status, remaining=int(left.group(1)) if left else None) for p in prices
    ]
    if not prices and price_text:  # "אירוע בהרשמה מראש": registration, price not stated
        tiers = [TicketTier(name="הרשמה", status=status)]
    place = card.place or extra.get("place")
    venue, address = _split_place(place)
    host = urlsplit(site.url).netloc.removeprefix("www.").split(".")[0]  # rosh-haayin
    city = _city(site, place)
    return ScrapedEvent(
        source=MuniEventsScraper.name,
        source_event_id=f"{host}:{card.event_id}",
        url=card.url,
        title=card.title,
        description=extra.get("description"),
        start_at=start,
        end_at=end,
        venue_name=venue,
        address=address,
        city=city,
        source_genres=card.tags or [site.category],
        organizer=site.organizer or (site.city if site.regional else f"עיריית {site.city}"),
        image_url=extra.get("image") or card.image,  # the page's full size, else the listing's thumbnail
        min_price=min(prices) if prices else None,
        max_price=max(prices) if prices else None,
        is_sold_out=sold_out,
        tickets=tiers,
    )
