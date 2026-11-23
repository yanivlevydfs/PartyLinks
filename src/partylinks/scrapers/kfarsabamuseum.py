"""Kfar Saba Museum (kfar-saba-museum.org, "מוזאון כפר סבא") - concerts and shows at Beit HaBe'er, family days and
children's shows in Baron Menashe's garden and the archaeological garden, workshops at the museum's workshop in
Beit Sapir.

The site is WordPress with the "Hello Tickets" plugin. Its calendar (/yoman-eruim/) is built from the public
``hello-tickets-calendar/v1/occurrences`` route: one call (``start`` today, ``end`` ``days_ahead`` on) gives every
dated performance - the occurrence id, the event post id, title, start and end, the place's name, the artwork,
``has_passed`` and ``status`` (a cancelled one is not imported, nor a title marked "נדחה", postponed). One
``wp/v2/hello_event`` call (``include`` the coming posts, ``_embed`` the terms and the artwork) adds the event
categories (ילדים, מבוגרים, מוזיקה, סדנה, הצגה, יוגה, פסטיבל בתים פתוחים, ...) and the full-size artwork; the
``event_location`` terms are the places. The WooCommerce store API (``wc/store/v1/products``, 100 a page, a page
or two) sells each performance's tiers as products named "<title> – <tier> (יום ..., ה-7 בדצמבר 2026 בשעה 09:30
– <place>)": the prices of a performance are the products with its title and day. Each event page (one request
per event) gives the text (``.ht-event-detail__description``). The event page is the ticket link (the site's own
shop). Each of the museum's places has its address and point in ``places``; another place line ("צריף הסנדלר,
הרצל 48 כפר סבא") is geocoded. robots.txt allows the site but /wp-admin/ and add-to-cart links.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from decimal import Decimal
from html import unescape
from typing import Any

from pydantic import BaseModel, Field
from selectolax.lexbor import LexborHTMLParser

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import clean_str, html_to_text, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_STAMP = "%Y-%m-%d %H:%M:%S"
_PASSED = re.compile(r"\s*\((?:הסתיים|הסתיימה)\)\s*$")
_WHEN = re.compile(r"\s*\(יום [^()]*\)\s*$")  # the product name's "(יום שני, ה-7 בדצמבר 2026 בשעה 09:30 – ...)"
_PRODUCT_DAY = re.compile(r"ה-(\d{1,2}) ב([א-ת]+) (\d{4})")  # "ה-7 בדצמבר 2026"
_HEBREW_MONTHS = {
    "ינואר": 1,
    "פברואר": 2,
    "מרץ": 3,
    "מרס": 3,
    "אפריל": 4,
    "מאי": 5,
    "יוני": 6,
    "יולי": 7,
    "אוגוסט": 8,
    "ספטמבר": 9,
    "אוקטובר": 10,
    "נובמבר": 11,
    "דצמבר": 12,
}


class Place(BaseModel):
    venue_name: str | None = None
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class KfarSabaMuseumSettings(SourceSettings):
    site_url: str = "https://kfar-saba-museum.org"
    occurrences_path: str = "/wp-json/hello-tickets-calendar/v1/occurrences"
    events_path: str = "/wp-json/wp/v2/hello_event"
    products_path: str = "/wp-json/wc/store/v1/products"
    days_ahead: int = Field(400, ge=30)
    products_per_page: int = Field(100, ge=1, le=100)
    max_product_pages: int = Field(4, ge=1)
    # A title word of a postponed performance (the site keeps it listed as "**נדחה** ...").
    postponed_words: list[str] = Field(default_factory=lambda: ["נדחה", "בוטל"])
    # Category terms that are no category ("אירועים קרובים": the home page's "coming" shelf).
    skip_categories: list[str] = Field(default_factory=lambda: ["אירועים קרובים"])
    # A category -> the words declared beside it (our genres read them).
    labels: dict[str, list[str]] = Field(default_factory=dict)
    # The museum's places by the occurrence's place name.
    places: dict[str, Place] = Field(default_factory=dict)
    city: str


class KfarSabaMuseumScraper(BaseScraper[KfarSabaMuseumSettings]):
    name = "kfarsabamuseum"
    settings_model = KfarSabaMuseumSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        tz = zone(self.timezone)
        today = datetime.now(tz).date()
        params = {
            "start": today.isoformat(),
            "end": (today + timedelta(days=s.days_ahead)).isoformat(),
            "status": "active",
        }
        rows = await self.get_json(s.site_url + s.occurrences_path, params=params)
        occurrences = [o for o in rows if isinstance(o, dict)] if isinstance(rows, list) else []
        coming = [o for o in occurrences if keep_occurrence(o, s, self.timezone)]
        coming.sort(key=lambda o: (str(o.get("start")), str(o.get("id"))))  # nearest first
        self.log.debug("%d occurrences, %d coming", len(occurrences), len(coming))
        if not coming:
            return
        post_ids = sorted({str(o.get("eventId")) for o in coming if o.get("eventId")})
        posts = await self.optional(
            "event posts",
            self.get_json(
                s.site_url + s.events_path,
                params={"include": ",".join(post_ids), "per_page": 100, "_embed": "wp:featuredmedia,wp:term"},
            ),
        )
        by_post = {str(p.get("id")): p for p in posts or [] if isinstance(p, dict)}
        products: list[dict[str, Any]] = []
        for index in range(s.max_product_pages):
            page = await self.optional(
                f"products page {index + 1}",
                self.get_json(
                    s.site_url + s.products_path,
                    params={"per_page": s.products_per_page, "page": index + 1, "orderby": "date", "order": "desc"},
                ),
            )
            rows = [p for p in page or [] if isinstance(p, dict)]
            products += rows
            if len(rows) < s.products_per_page:
                break
        texts: dict[str, str | None] = {}
        for occurrence in coming:
            post = by_post.get(str(occurrence.get("eventId")), {})
            url = str(occurrence.get("url") or post.get("link") or "")
            if url and url not in texts:
                page_html = await self.optional(f"event page {url}", self.get_text(url))
                texts[url] = page_text(page_html) if page_html else None
            try:
                event = parse_occurrence(occurrence, post, products, texts.get(url), s, self.timezone)
            except Exception as exc:  # noqa: BLE001 - one odd row never drops the others
                self.record_failure(f"occurrence {occurrence.get('id')}", exc)
                continue
            if event and is_current(
                event.start_at, event.end_at, RetentionSettings(), self.timezone, time_known=event.time_known
            ):
                yield event


def keep_occurrence(occurrence: dict[str, Any], s: KfarSabaMuseumSettings, timezone: str) -> bool:
    """A coming, active, not-postponed performance with a title and a start."""
    extended = occurrence.get("extendedProps") if isinstance(occurrence.get("extendedProps"), dict) else {}
    title = str(occurrence.get("title") or "")
    if not title or not occurrence.get("start") or extended.get("has_passed"):
        return False
    if str(occurrence.get("status") or "active") != "active":
        return False
    return not any(w in title for w in s.postponed_words)


def parse_occurrence(
    occurrence: dict[str, Any],
    post: dict[str, Any],
    products: list[dict[str, Any]],
    text: str | None,
    s: KfarSabaMuseumSettings,
    timezone: str,
) -> ScrapedEvent | None:
    """One occurrence (with its event post, the store's products and the page text) -> its event."""
    tz = zone(timezone)
    title = clean_str(_PASSED.sub("", unescape(str(occurrence.get("title") or ""))))
    url = str(occurrence.get("url") or post.get("link") or "")
    if not title or not url.startswith("http"):
        return None
    start = datetime.strptime(str(occurrence["start"]), _STAMP).replace(tzinfo=tz)
    end = None
    if occurrence.get("end"):
        end = datetime.strptime(str(occurrence["end"]), _STAMP).replace(tzinfo=tz)
        if end <= start:
            end = None
    place_name = clean_str(unescape(str(occurrence.get("location") or "")))
    place = s.places.get(place_name or "")
    terms = [t for t in _terms(post, "event_category") if t not in s.skip_categories]
    declared: list[str] = []
    for term in terms:
        declared += [term, *s.labels.get(term, [])]
    images = [
        str(m["source_url"])
        for m in (post.get("_embedded") or {}).get("wp:featuredmedia") or []
        if isinstance(m, dict) and str(m.get("source_url") or "").startswith("http")
    ]
    extended = occurrence.get("extendedProps") if isinstance(occurrence.get("extendedProps"), dict) else {}
    if not images and str(extended.get("image") or "").startswith("http"):
        images = [str(extended["image"])]
    tickets = product_tiers(products, title, start.date())
    prices = [t.price for t in tickets if t.price is not None]
    sold_out = str(extended.get("availability_status") or "") in ("sold_out", "soldout")
    return ScrapedEvent(
        source=KfarSabaMuseumScraper.name,
        source_event_id=str(occurrence.get("id")),
        url=url,
        title=title,
        description=text,
        start_at=start,
        end_at=end,
        time_known=True,
        venue_name=(place.venue_name if place and place.venue_name else place_name) or "מוזאון כפר סבא",
        address=(place.address if place else place_name) or None,
        city=s.city,
        latitude=place.latitude if place else None,
        longitude=place.longitude if place else None,
        source_genres=list(dict.fromkeys(g.lower() for g in declared)),
        organizer="מוזאון כפר סבא",
        images=images,
        tickets=tickets,
        min_price=min(prices, default=None),
        max_price=max(prices, default=None),
        is_sold_out=sold_out,
        extra={"purchase_url": url, "event_id": str(occurrence.get("eventId") or "")},
    )


def product_tiers(products: list[dict[str, Any]], title: str, day: date) -> list[TicketTier]:
    """The store's tiers of one performance: products named "<title> – <tier> (יום ..., ה-7 בדצמבר 2026 ...)"
    (the store writes an en dash where the calendar may write a hyphen)."""
    tiers: dict[str, TicketTier] = {}
    key = _dashes(title)
    for product in products:
        name = _dashes(clean_str(unescape(str(product.get("name") or ""))) or "")
        if not name.startswith(key) or product_day(name) != day:
            continue
        tier = clean_str(_WHEN.sub("", name[len(key) :]).strip(" -")) or "כרטיס"
        prices = product.get("prices") if isinstance(product.get("prices"), dict) else {}
        try:
            minor = int(prices.get("currency_minor_unit") or 2)
            price = Decimal(str(prices.get("price") or "0")) / (Decimal(10) ** minor)
        except (ArithmeticError, ValueError):
            continue
        tiers.setdefault(tier, TicketTier(name=tier, price=price, is_free=price == 0))
    return list(tiers.values())


def _dashes(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("–", "-").replace("—", "-")).strip()


def product_day(name: str) -> date | None:
    """The performance day in a product's name ("ה-7 בדצמבר 2026")."""
    match = _PRODUCT_DAY.search(name)
    month = _HEBREW_MONTHS.get(match.group(2)) if match else None
    if not match or not month:
        return None
    try:
        return date(int(match.group(3)), month, int(match.group(1)))
    except ValueError:
        return None


def page_text(html: str) -> str | None:
    """The event page's text (``.ht-event-detail__description``)."""
    node = LexborHTMLParser(html).css_first(".ht-event-detail__description")
    return html_to_text(node.html) if node is not None and node.html else None


def _terms(post: dict[str, Any], taxonomy: str) -> list[str]:
    names: list[str] = []
    for group in (post.get("_embedded") or {}).get("wp:term") or []:
        for term in group if isinstance(group, list) else []:
            if isinstance(term, dict) and term.get("taxonomy") == taxonomy:
                name = clean_str(unescape(str(term.get("name") or "")))
                if name:
                    names.append(name)
    return names
