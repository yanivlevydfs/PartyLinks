"""HaMesila (hamesila.org.il, "המסילה ב׳ - תרבות, פנאי וקהילה") - a community and culture house in Herzliya
(HaZamar HaIvri 5): lectures, family shows, tribute concerts, an exhibition.

The site is WordPress + Elementor. Its custom post type ``events`` ("אירועים במסילה") lists every event in one
wp-json call (``list_path``: id, publish date, link, title, ``event_taxonomy`` ids; ~10 posts, ``X-WP-Total``
for more pages). The list has no event dates: posts not modified for ``max_age_days`` are past and left out
(``modified_after``; the site deletes most past posts anyway). The taxonomy's names (``taxonomy_path``, one
more call: "חודש הקיימות") are the source genres. The day, price and seller are only on the event page (one
request each), in the single-post template: a heading (the title), a text widget with the day ("יום שלישי |
1/12 20:00", "יום שלישי | 2.2.2027 | 20:00", the doors after it), a text widget with the price ("עלות: 55 ₪",
"כרטיס יחיד: 80 ₪ כרטיס זוגי: 150 ₪", "ללא תשלום"), the seller's button and the text. A day without a year
is in the year the post was published, or the next when that is over ``past_grace_days`` before it.

Most events are sold on the house's Smarticket box office (``hamesila-bet``, read by ``smarticket``): the
button's link is kept as ``purchase_url`` so dedup merges the two. The lectures sold elsewhere (tarbuta.com, a
lecturer's own site) are only here. Pages with no day are skipped: the exhibition (opening hours only) and
the hub pages that list other events ("קיימות במסילה"). No end time. Courses and workshops are another post
type (``course_workshop``): not read. One venue: its pin is in config.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from decimal import Decimal
from html import unescape
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from pydantic import Field
from selectolax.lexbor import LexborHTMLParser, LexborNode

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import clean_str, html_to_text, is_site_logo, og_image, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_DAY = re.compile(r"(?<![\d:.])(\d{1,2})[./](\d{1,2})(?:[./](\d{4}))?(?![\d:])")
_CLOCK = re.compile(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)")
_PRICE = re.compile(r"(?:([^:₪\d]+?)\s*:\s*)?(\d+(?:\.\d+)?)\s*₪")


class HaMesilaSettings(SourceSettings):
    site_url: str = "https://hamesila.org.il"
    list_path: str = "/wp-json/wp/v2/events"
    list_fields: str = "id,date,link,title,event_taxonomy"
    per_page: int = 100
    max_pages: int = 10
    # Posts not touched for this long are past events (the list has no event dates): their pages are not read.
    max_age_days: int = 365
    taxonomy_path: str = "/wp-json/wp/v2/event_taxonomy?per_page=100&_fields=id,name"
    venue_name: str = "המסילה ב׳ - תרבות, פנאי וקהילה"
    address: str = "הזמר העברי 5, הרצליה"
    city: str = "הרצליה"
    latitude: float | None = None
    longitude: float | None = None
    # The doors line after the start ("פתיחת דלתות: 19:30"): its clock is not the start.
    doors_words: list[str] = Field(default_factory=lambda: ["פתיחת דלתות"])
    free_words: list[str] = Field(default_factory=lambda: ["ללא תשלום", "כניסה חופשית", "חינם"])
    couple_words: list[str] = Field(default_factory=lambda: ["זוגי"])
    ticket_name: str = "כרטיס"
    free_ticket_name: str = "כניסה חופשית"
    # Links on the page that are no seller: the site itself and social profiles.
    not_sellers: list[str] = Field(
        default_factory=lambda: [
            "hamesila.org.il",
            "facebook.com",
            "instagram.com",
            "whatsapp.com",
            "youtube.com",
            "wa.me",
        ]
    )
    # A day with no year is never this many days before the post's publication (it is next year's).
    past_grace_days: int = 30


class HaMesilaScraper(BaseScraper[HaMesilaSettings]):
    name = "hamesila"
    settings_model = HaMesilaSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        since = (datetime.now(zone(self.timezone)) - timedelta(days=s.max_age_days)).strftime("%Y-%m-%dT%H:%M:%S")

        async def page(index: int) -> tuple[list[dict[str, Any]], str | None]:
            params = {"per_page": s.per_page, "page": index + 1, "_fields": s.list_fields, "modified_after": since}
            response = await self.request("GET", s.site_url + s.list_path, params=params)
            rows = response.json()
            found = [r for r in rows if isinstance(r, dict) and r.get("id")] if isinstance(rows, list) else []
            return found, response.headers.get("X-WP-Total")

        posts = await self.paginate(page, s.per_page, s.max_pages)
        terms = await self.optional("event taxonomy", self.get_json(s.site_url + s.taxonomy_path, expect=list)) or []
        names = {t.get("id"): clean_str(t.get("name")) for t in terms if isinstance(t, dict)}
        self.log.debug("listed %d posts", len(posts))

        async def read(post: dict[str, Any]) -> ScrapedEvent | None:
            page = await self.get_text(str(post["link"]))
            return parse_event(post, page, names, s, self.timezone)

        events = [
            event
            async for event in self.stream_isolated(posts, read, label=lambda p: f"post {p.get('id')}")
            if is_current(event.start_at, event.end_at, RetentionSettings(), self.timezone, time_known=event.time_known)
        ]
        for event in sorted(events, key=lambda e: e.start_at or datetime.max.replace(tzinfo=zone(self.timezone))):
            yield event  # nearest first


def parse_event(
    post: dict[str, Any], page_html: str, taxonomy: dict[Any, str | None], s: HaMesilaSettings, timezone: str
) -> ScrapedEvent | None:
    """One ``events`` post and its page -> the event; None for a page with no day (exhibition, hub page)."""
    root = LexborHTMLParser(page_html).css_first('[data-elementor-type="single-post"]')
    if root is None or not post.get("id") or not post.get("link"):
        return None
    title = clean_str(unescape(str((post.get("title") or {}).get("rendered") or "")))
    if not title:
        heading = root.css_first(".elementor-widget-heading")
        title = clean_str(heading.text(separator=" ")) if heading else None
    texts = [t for n in root.css(".elementor-widget-text-editor") if (t := clean_str(n.text(separator=" ")))]
    when = next((t for t in texts if _DAY.search(t)), None)
    published = _published(post.get("date"))
    start = _start(when, published, s) if when and published else None
    if not title or start is None:
        return None
    start_at, time_known = start[0].replace(tzinfo=zone(timezone)), start[1]
    price_text = next((t for t in texts if t != when and ("₪" in t or any(w in t for w in s.free_words))), None)
    tickets = parse_prices(price_text, s) if price_text else []
    prices = [t.price for t in tickets if t.price is not None]
    seller = _seller(root, s)
    body = root.css_first(".elementor-widget-theme-post-content")
    image = og_image(page_html)
    genres = [g.lower() for i in post.get("event_taxonomy") or [] if (g := taxonomy.get(i))]
    return ScrapedEvent(
        source=HaMesilaScraper.name,
        source_event_id=str(post["id"]),
        url=str(post["link"]),
        title=title,
        description=html_to_text(body.html) if body is not None else None,
        start_at=start_at,
        time_known=time_known,
        venue_name=s.venue_name,
        address=s.address,
        city=s.city,
        latitude=s.latitude,
        longitude=s.longitude,
        source_genres=genres,
        images=[image] if image and not is_site_logo(image) else [],
        tickets=tickets,
        min_price=min(prices) if prices else None,
        max_price=max(prices) if prices else None,
        extra={"purchase_url": seller} if seller else {},
    )


def _published(value: Any) -> date | None:
    try:
        return datetime.fromisoformat(str(value)).date()
    except ValueError:
        return None


def _start(text: str, published: date, s: HaMesilaSettings) -> tuple[datetime, bool] | None:
    """(start, time known) of the date line; the doors' clock after "פתיחת דלתות" is not the start."""
    day = _DAY.search(text)
    if not day:
        return None
    rest = text
    for word in s.doors_words:
        rest = rest.split(word, 1)[0]
    clock = _CLOCK.search(rest[day.end() :]) or _CLOCK.search(rest)
    year = int(day[3]) if day[3] else None
    try:
        if year is None:
            found = date(published.year, int(day[2]), int(day[1]))
            if found < published - timedelta(days=s.past_grace_days):
                found = date(published.year + 1, found.month, found.day)
        else:
            found = date(year, int(day[2]), int(day[1]))
    except ValueError:
        return None
    hour, minute = (int(clock[1]), int(clock[2])) if clock else (0, 0)
    return datetime(found.year, found.month, found.day, hour, minute), clock is not None


def parse_prices(text: str, s: HaMesilaSettings) -> list[TicketTier]:
    """The price widget -> tiers: "עלות: 55 ₪", "כרטיס יחיד: 80 ₪ כרטיס זוגי: 150 ₪", "ללא תשלום"."""
    tiers = []
    for label, amount in _PRICE.findall(text):
        name = clean_str(label)
        if not name or name.startswith("עלות"):
            name = s.ticket_name
        couple = any(w in name for w in s.couple_words)
        tiers.append(TicketTier(name=name, price=Decimal(amount), admits=2 if couple else 1))
    if not tiers and any(w in text for w in s.free_words):
        tiers.append(TicketTier(name=s.free_ticket_name, price=Decimal(0), is_free=True))
    return tiers


def _seller(root: LexborNode, s: HaMesilaSettings) -> str | None:
    """The first button that leads off the site (Smarticket, tarbuta.com), without its tracking parameters."""
    for a in root.css(".elementor-widget-button a[href]"):
        href = clean_str(a.attributes.get("href"))
        if not href or not href.startswith(("http://", "https://")):
            continue
        parts = urlparse(href)
        host = parts.netloc.lower().removeprefix("www.")
        if any(host == h or host.endswith("." + h) for h in s.not_sellers):
            continue
        query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith("utm_")])
        return urlunparse(parts._replace(query=query))
    return None
