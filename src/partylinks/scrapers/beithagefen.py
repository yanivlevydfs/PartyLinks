"""Beit HaGefen (beit-hagefen.com, "בית הגפן") - the Arab-Jewish culture centre in Wadi Nisnas, Haifa, and its
yearly "חג החגים" festival (haifahag.com, December).

Both sites run one ASP.NET CMS (Fusion Studio), so one reader serves both (``sites`` in config). No API, feed or
sitemap; robots.txt has only Cloudflare's content signals. Listing: ``AllEvents.aspx`` holds every coming item
as a card (``li.item``: title, first date and time, hall, artwork, its category in the link's
``CategoryItemId``); the page's own filter buttons name the categories ("מופעים", "מתחם משפחות", "תערוכות",
"סיורים", "קונצרטים"), generic ones ("כל האירועים") left out. A card's link names one category only (or the
generic one), so each named category's filtered page (``AllEvents.aspx?CategoryItemId=2``: its items only) is read
too and every category an item is in is kept ("Silent Light": מופעים and קונצרטים). Each item's page
(``EventDetalis.aspx``) gives one line per performance ("תאריך, שעה - 18-12-2026 - 13:00", each with its own
ticket link on the house's site), the hall, street address, price ("50 ₪", "0 ₪" = free), the seller's button
(mostly Eventer: kept as ``extra["purchase_url"]``, so dedup ties it to the eventer source's listing), the venue's
map pin (Google Maps embed) and the text. One request per site and per named category plus one per item (~60 in
December, a few the rest of the year).

The house's halls (hall, lobby, yard, gallery, library, studio, Al-Karma) are one venue; tours whose meeting
point is given "after buying" are placed at the house, as Eventer places them. The free yard shows, Santa's
house and the exhibitions of the festival are sold nowhere else. One event per performance, soonest first;
an item both sites list (the house's festival shows) is kept once, the house's own page first.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from urllib.parse import parse_qs, urljoin, urlparse

from pydantic import BaseModel, Field
from selectolax.lexbor import LexborHTMLParser, LexborNode

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import ParseError, clean_str, html_to_text, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceBlockedError, SourceSettings

_SHOW = re.compile(r"(\d{1,2})-(\d{1,2})-(\d{4})(?:\s*-\s*(\d{1,2}):(\d{2}))?")
_IMAGE = re.compile(r"url\(\s*['\"]?([^'\")]+)")
_PIN = re.compile(r"!2d(-?\d+\.\d+)!3d(-?\d+\.\d+)")  # Google Maps embed: !2d<longitude>!3d<latitude>
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


class BeitHagefenSite(BaseModel):
    key: str  # source_event_id prefix: one CMS, two id spaces
    site_url: str
    listing_path: str
    genres: list[str] = Field(default_factory=list)  # added to every item's own category ("פסטיבל")


class BeitHagefenSettings(SourceSettings):
    site_url: str  # the house's site (the stores page)
    # The house's site first: its pages give each performance its own ticket link and the right hour.
    sites: list[BeitHagefenSite]
    venue_name: str
    address: str
    city: str
    latitude: float | None = None
    longitude: float | None = None
    organizer: str
    house_words: list[str] = Field(default_factory=list)  # a hall naming one of these is the house
    placeholder_venues: list[str] = Field(default_factory=list)  # "given after buying": met at the house
    generic_categories: list[str] = Field(default_factory=list)  # filter buttons that are no category
    social_hosts: list[str] = Field(default_factory=list)  # a seller button to these is no ticket link


@dataclass
class Card:
    site: BeitHagefenSite
    item_id: str
    url: str
    title: str
    first_day: date | None
    image: str | None
    categories: list[str] = field(default_factory=list)


class BeitHagefenScraper(BaseScraper[BeitHagefenSettings]):
    name = "beithagefen"
    settings_model = BeitHagefenSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        cards: list[Card] = []
        errors: list[Exception] = []
        for site in s.sites:
            try:
                page = await self.get_text(site.site_url + site.listing_path)
                listed = parse_listing(page, site, tuple(s.generic_categories))
                await self._add_categories(listed, category_pages(page, site, tuple(s.generic_categories)))
                cards += listed
            except SourceBlockedError:
                raise
            except Exception as exc:  # noqa: BLE001 - one site down must not cost the other
                errors.append(exc)
                self.record_failure(f"listing {site.key}", exc)
        if errors and len(errors) == len(s.sites):
            raise errors[0]
        cards.sort(key=lambda c: c.first_day or date.max)  # nearest first
        self.log.debug("listed %d items", len(cards))

        async def fetch(card: Card) -> list[ScrapedEvent]:
            return parse_event(await self.get_text(card.url), card, s, self.timezone)

        events: list[ScrapedEvent] = []
        async for found in self.stream_isolated(cards, fetch, label=lambda c: c.url):
            events += found
        nearest = sorted(
            one_page_per_show(events, s, self.timezone), key=lambda e: e.start_at.timestamp() if e.start_at else 0
        )
        for event in nearest:
            if is_current(
                event.start_at, event.end_at, RetentionSettings(), self.timezone, time_known=event.time_known
            ):
                yield event

    async def _add_categories(self, cards: list[Card], pages: dict[str, str]) -> None:
        """Every named category an item is in (one filtered page each); one page failing costs only its label."""
        for label, url in pages.items():
            try:
                found = listed_item_ids(await self.get_text(url))
            except SourceBlockedError:
                raise
            except Exception as exc:  # noqa: BLE001 - the cards keep the category their own link names
                self.record_failure(f"category {label}", exc)
                continue
            for card in cards:
                if card.item_id in found and label not in card.categories:
                    card.categories.append(label)


def one_page_per_show(events: list[ScrapedEvent], s: BeitHagefenSettings, timezone: str) -> list[ScrapedEvent]:
    """A show both sites list (one ticket link, one day) is kept from the first site in ``sites`` only; one site's
    performances of a day sharing a link (a workshop at 13:00 and at 15:00) all stay."""
    order = {site.key: n for n, site in enumerate(s.sites)}
    first_site: dict[tuple[str, date], str] = {}
    kept = []
    for event in sorted(events, key=lambda e: order.get(_site_key(e), len(order))):
        link, key = event.extra.get("purchase_url"), _site_key(event)
        if (
            link
            and event.start_at
            and first_site.setdefault((link, event.start_at.astimezone(zone(timezone)).date()), key) != key
        ):
            continue
        kept.append(event)
    return kept


def _site_key(event: ScrapedEvent) -> str:
    return event.source_event_id.split(":", 1)[0]


def _text(node: LexborNode | None) -> str:
    return " ".join((node.text() if node else "").split())


def _image(node: LexborNode | None, page_url: str) -> str | None:
    found = _IMAGE.search(node.attributes.get("style") or "") if node else None
    return urljoin(page_url, found.group(1).strip()) if found else None


def _labels(tree: LexborHTMLParser, generic: tuple[str, ...]) -> dict[str, tuple[str, str]]:
    """The page's filter buttons: category id -> (label, href), generic ones ("כל האירועים") left out."""
    labels: dict[str, tuple[str, str]] = {}
    for button in tree.css("a.filter[href]"):
        href = button.attributes.get("href") or ""
        category = parse_qs(urlparse(href).query).get("CategoryItemId", [""])[0]
        label = _text(button)
        if category and label and label not in generic:
            labels.setdefault(category, (label, href))
    return labels


def category_pages(page_html: str, site: BeitHagefenSite, generic: tuple[str, ...] = ()) -> dict[str, str]:
    """AllEvents.aspx -> each named category's filtered page: label -> URL."""
    page_url = site.site_url + site.listing_path
    pages: dict[str, str] = {}
    for label, href in _labels(LexborHTMLParser(page_html), generic).values():
        pages.setdefault(label, urljoin(page_url, href))
    return pages


def listed_item_ids(page_html: str) -> set[str]:
    """A (filtered) AllEvents page -> the ids of the items its cards link to."""
    ids = set()
    for link in LexborHTMLParser(page_html).css("li.item a.item_container[href]"):
        ids.update(parse_qs(urlparse(link.attributes.get("href") or "").query).get("EventItemId", []))
    return ids


def parse_listing(page_html: str, site: BeitHagefenSite, generic: tuple[str, ...] = ()) -> list[Card]:
    """AllEvents.aspx -> one card per item, each once, with the category its own link names (filter buttons)."""
    tree = LexborHTMLParser(page_html)
    page_url = site.site_url + site.listing_path
    labels = {category: label for category, (label, _) in _labels(tree, generic).items()}
    cards: dict[str, Card] = {}
    for item in tree.css("li.item"):
        link = item.css_first("a.item_container[href]")
        href = clean_str(link.attributes.get("href")) if link else None
        query = parse_qs(urlparse(href or "").query)
        item_id = query.get("EventItemId", [""])[0]
        if not href or not item_id or item_id in cards:
            continue
        day = _SHOW.search(_text(item.css_first(".item_caption_detalis_date")))
        category = labels.get(query.get("CategoryItemId", [""])[0])
        cards[item_id] = Card(
            site=site,
            item_id=item_id,
            url=urljoin(page_url, href),
            title=_text(item.css_first(".item_caption_name")),
            first_day=date(int(day[3]), int(day[2]), int(day[1])) if day else None,
            image=_image(item.css_first(".filter_item_img"), page_url),
            categories=[category] if category else [],
        )
    return list(cards.values())


def _field(page: LexborNode, icon: str) -> str:
    """The value beside one icon of the info box ("fa-map-marker" -> "אולם בית הגפן"), its label left out."""
    for box in page.css(".events_page_id_info_icon"):
        if box.css_first(f"i.{icon}") is None:
            continue
        text = box.css_first(".events_page_id_info_text")
        if text is None:
            return ""
        for label in text.css("strong"):
            label.decompose()
        return _text(text)
    return ""


def _purchase(href: str | None, s: BeitHagefenSettings) -> str | None:
    link = clean_str(href)
    if not link or not link.startswith("http"):
        return None
    host = (urlparse(link).hostname or "").removeprefix("www.")
    return None if any(host == h or host.endswith("." + h) for h in s.social_hosts) else link


def parse_event(page_html: str, card: Card, s: BeitHagefenSettings, timezone: str) -> list[ScrapedEvent]:
    """An item's page -> one event per performance; [] when it gives no date."""
    page = LexborHTMLParser(page_html).css_first(".events_page_id_section")
    if page is None:
        raise ParseError(f"{card.url}: no event block")
    title = _text(page.css_first(".events_page_id_name h1")) or card.title
    if not title:
        raise ParseError(f"{card.url}: no title")
    tz = zone(timezone)
    button = page.css_first("a.events_page_id_info_btn[href]")
    default_link = _purchase(button.attributes.get("href") if button else None, s)
    shows: dict[datetime, tuple[bool, str | None]] = {}
    for option in page.css(".events_page_id_info_drop option"):
        found = _SHOW.search(_text(option))
        if not found:
            continue
        d, m, y, hh, mm = found.groups()
        start = datetime.combine(date(int(y), int(m), int(d)), time(int(hh) % 24, int(mm)) if hh else time(), tz)
        shows.setdefault(start, (bool(hh), _purchase(option.attributes.get("data-ticketurl"), s) or default_link))
    if not shows:
        return []

    hall = clean_str(_field(page, "fa-map-marker"))
    address = clean_str(_field(page, "fa-location-arrow"))
    pin = _PIN.search(next((f.attributes.get("src") or "" for f in page.css(".events_page_id_des_map iframe")), ""))
    lng, lat = (float(pin[1]), float(pin[2])) if pin else (None, None)
    if lat is not None and lng is not None and not (29 < lat < 34 and 34 < lng < 36):  # not a pin in Israel
        lat = lng = None
    extra: dict[str, str] = {}
    if not hall or hall in s.placeholder_venues or any(word in hall for word in s.house_words):
        if hall and hall not in s.placeholder_venues and hall != s.venue_name:
            extra["hall"] = hall
        venue, address, lat, lng = s.venue_name, s.address, s.latitude, s.longitude
    else:
        venue = hall
        for suffix in (", " + s.city, " " + s.city):
            address = address.removesuffix(suffix) if address else None
    prices = sorted({Decimal(n) for n in _NUMBER.findall(_field(page, "fa-credit-card-alt"))})
    free = prices == [Decimal(0)]
    if free:
        tiers = [TicketTier(name="כניסה חופשית", price=0, is_free=True)]
    else:
        tiers = [TicketTier(name="כרטיס", price=p) for p in prices if p > 0]
    paid = [t.price for t in tiers if t.price]
    node = page.css_first("p.events_page_id_des_text")
    description = html_to_text(node.html) if node else None
    first_line, _, rest = (description or "").partition("\n")
    if " ".join(first_line.split()) == title:  # the text opens with the title again
        description = rest.strip() or None
    image = _image(page.css_first(".events_page_id_img"), card.url) or card.image
    first = ScrapedEvent(
        source=BeitHagefenScraper.name,
        source_event_id="",
        url=card.url,
        title=title,
        description=description or None,
        start_at=next(iter(shows)),
        venue_name=venue,
        address=address,
        city=s.city,
        latitude=lat,
        longitude=lng,
        source_genres=[g.lower() for g in dict.fromkeys([*card.categories, *card.site.genres])],
        organizer=s.organizer,
        image_url=image,
        images=[image] if image else [],
        min_price=Decimal(0) if free else (paid[0] if paid else None),
        max_price=Decimal(0) if free else (paid[-1] if paid else None),
        tickets=tiers,
        extra=extra,
    )
    return [
        first.model_copy(
            update={
                "source_event_id": f"{card.site.key}:{card.item_id}:{start:%Y%m%d%H%M}",
                "start_at": start,
                "time_known": known,
                "extra": {**extra, "purchase_url": link} if link else dict(extra),
            },
            deep=True,
        )
        for start, (known, link) in shows.items()
    ]
