"""ZOA House / בית ציוני אמריקה (zoatlv.co.il) - culture house at Daniel Frisch 1, Tel Aviv.

A WordPress theme with an ``event`` post type that is not in the REST API and has no JSON-LD Event, so
the HTML is read. The archive (``/event/``, ``/event/page/N/``, 30 a page) lists upcoming events only,
soonest first: ``a.event-loop`` cards with the link, artwork, day ("30") and month ("אוקטובר 2026"), start
time, hall ("מרתה", "מאירהוף"), title, subtitle and the site's categories ("הופעה, סטנדאפ ובידור"). Its page
count runs past the last real page, which redirects to page 1: a page that adds nothing ends the walk.
Each event's page adds the text and the seller's link ("לאתר האירוע": Comy, Comedy Bar, Smarticket box
offices, Leaan, ZOA's own EASYTIX box office, course sign-ups on tinyurl...), kept as
``extra["purchase_url"]`` in the encoding the model gives every URL (Leaan's own listing URL is
``/events/<Hebrew slug, percent-encoded>/<id>``), so dedup ties the two. The site shows no prices: a seller
in ``price_hosts`` (Comedy Bar and other Smarticket box offices, Tickchak, Tarbuta) is read once more for its
JSON-LD ``offers``; else a price the text writes out ("עלות: 999 ₪") is used (text_prices). The other
sellers' own sources bring theirs to the group. Exhibitions (``/exhibition/``) are not read.

The archive stops short: its pagination links pages that redirect to ``/event/`` (6 real pages, ~180
cards, November 2026), so later events (a weekly series into 2027) are missing. So the Yoast
``event-sitemap*.xml`` files changed lately are read too, and each event page they list (``lastmod`` within
``sitemap_max_age_days``) that the archive lacks is read on its own: its page carries the same date, hall,
time and artwork as a card (no categories). Past ones are dropped.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from urllib.parse import unquote, urlsplit

from pydantic import Field, HttpUrl
from selectolax.lexbor import LexborHTMLParser, LexborNode

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier, TierStatus
from partylinks.parsing import (
    ParseError,
    clean_str,
    html_to_text,
    jsonld_nodes,
    parse_iso,
    read_clock,
    to_decimal,
    zone,
)
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.text_prices import text_tickets
from partylinks.search import MONTHS

_MONTH_YEAR = re.compile(r"(\S+)\s+(\d{4})")
_PAGE = re.compile(r"/page/(\d+)/?$")
_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>\s*(?:<lastmod>\s*([^<\s]+)\s*</lastmod>)?")


class ZoaTlvSettings(SourceSettings):
    site_url: str
    listing_path: str = "/event/"
    page_path: str = "/event/page/{page}/"
    max_pages: int = Field(30, ge=1)
    # The Yoast sitemap index; its event-sitemap*.xml files list every event page (the archive stops short).
    sitemap_path: str = "/sitemap_index.xml"
    sitemap_prefix: str = "event-sitemap"
    # Sitemap files and event pages changed within this many days are read (older ones are past events).
    sitemap_max_age_days: int = Field(120, ge=1)
    # The seller's white-label host -> the host its listing (another source) is read from:
    # tickets.zoatlv.co.il is ZOA's own EASYTIX box office, listed at easytix.co.il/event/<account>/<event>.
    seller_hosts: dict[str, str] = Field(default_factory=dict)
    # Sellers whose event page has JSON-LD ``offers`` (host or parent domain): one extra request per event.
    price_hosts: list[str] = Field(
        default_factory=lambda: ["tickets.comedybar.net", "smarticket.co.il", "tickchak.co.il", "tarbuta.com"]
    )
    venue_name: str
    address: str
    city: str
    latitude: float | None = None
    longitude: float | None = None


class ZoaTlvScraper(BaseScraper[ZoaTlvSettings]):
    name = "zoatlv"
    settings_model = ZoaTlvSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        first = await self.get_text(s.site_url + s.listing_path)
        cards = {c["url"]: c for c in parse_listing(first)}
        if not cards:
            raise ParseError("no a.event-loop card on the listing page (markup changed?)")
        for page in range(2, min(last_page(first), s.max_pages) + 1):
            html = await self.optional(
                f"listing page {page}", self.get_text(s.site_url + s.page_path.format(page=page))
            )
            new = [c for c in parse_listing(html or "") if c["url"] not in cards]
            if not new:
                break  # past the last real page (it redirects to page 1)
            cards.update((c["url"], c) for c in new)
        self.log.debug("listed %d events", len(cards))
        unlisted = [u for u in await self._sitemap_urls() if u not in cards]
        self.log.debug("%d more event pages in the sitemap", len(unlisted))
        events = []
        for card in cards.values():
            try:
                event = parse_card(card, s, self.timezone)
            except Exception as exc:  # noqa: BLE001 - one odd card never drops the others
                self.record_failure(f"event {unquote(card['url'])}", exc)
                continue
            if event is not None:
                events.append(event)
            else:
                problem = ParseError(f"no date or title: {card['day']} {card['month']}")
                self.record_failure(f"event {unquote(card['url'])}", problem)

        async def detail(item: ScrapedEvent | str) -> ScrapedEvent | None:
            if isinstance(item, str):  # a sitemap page the archive lacks: the page is the card
                page = await self.get_text(item)
                event = parse_card(page_card(page, item), s, self.timezone)
                if event is None or not is_current(event.start_at, None, RetentionSettings(), self.timezone):
                    return None  # past, or no event page (no date block)
            else:
                event = item
                page = await self.optional(f"page {unquote(str(event.url))}", self.get_text(str(event.url)))
            if page:
                add_page(event, page, s.seller_hosts)
            seller = (event.extra or {}).get("purchase_url")
            host = urlsplit(seller or "").hostname or ""
            if seller and any(host == h or host.endswith("." + h) for h in s.price_hosts):
                try:
                    event.tickets = seller_tickets(await self.get_text(seller))
                except Exception as exc:  # noqa: BLE001 - the seller's price is a bonus, never the event's failure
                    self.log.debug("no price from %s: %s", seller, exc)
            event.tickets = event.tickets or text_tickets(event.description)
            return event

        items: list[ScrapedEvent | str] = [*events, *unlisted]
        label = lambda i: unquote(i if isinstance(i, str) else str(i.url))
        async for event in self.stream_isolated(items, detail, label=label):
            yield event

    async def _sitemap_urls(self) -> list[str]:
        """Event pages in the event sitemaps changed within ``sitemap_max_age_days``."""
        s = self.settings
        since = datetime.now(UTC) - timedelta(days=s.sitemap_max_age_days)
        index = await self.optional("sitemap index", self.get_text(s.site_url + s.sitemap_path))
        urls: list[str] = []
        for sitemap in recent_locs(index or "", since):
            if urlsplit(sitemap).path.rsplit("/", 1)[-1].startswith(s.sitemap_prefix):
                xml = await self.optional(f"sitemap {sitemap}", self.get_text(sitemap))
                urls += recent_locs(xml or "", since)
        listing = urlsplit(s.site_url + s.listing_path).path
        return list(dict.fromkeys(u for u in urls if (p := urlsplit(u).path).startswith(listing) and p != listing))


def last_page(page_html: str) -> int:
    """The highest page number the pagination links to (1 when there is none)."""
    numbers = [
        int(m.group(1))
        for a in LexborHTMLParser(page_html).css("nav.pagination a.page-numbers[href]")
        if (m := _PAGE.search(a.attributes.get("href") or ""))
    ]
    return max(numbers, default=1)


def recent_locs(xml: str, since: datetime) -> list[str]:
    """A sitemap's (or index's) URLs whose ``lastmod`` is ``since`` or later."""
    return [
        url for url, lastmod in _LOC.findall(xml) if (day := parse_iso(lastmod, "UTC")) is not None and day >= since
    ]


def page_card(page_html: str, url: str) -> dict[str, str]:
    """An event's own page -> the dict ``parse_listing`` gives (no categories on the page)."""
    tree = LexborHTMLParser(page_html)
    info = tree.css_first(".event-details .event-info")
    image = tree.css_first(".event-img img")
    return {
        "url": url,
        "title": _text(tree.root, "h1.title1") if tree.root else "",
        "subtitle": "",
        "day": _text(info, ".event-date .day") if info else "",
        "month": _text(info, ".event-date .my") if info else "",
        "time": _text(info, ".event-time") if info else "",
        "hall": _text(info, ".hall-name") if info else "",
        "tags": "",
        "image": (image.attributes.get("src") or "") if image else "",
    }


def _text(node: LexborNode, selector: str) -> str:
    found = node.css_first(selector)
    return (clean_str(found.text()) or "") if found else ""


def parse_listing(page_html: str) -> list[dict[str, str]]:
    """Archive page -> one dict per event: url, title, subtitle, day, month, time, hall, tags, image."""
    cards: dict[str, dict[str, str]] = {}
    for node in LexborHTMLParser(page_html).css("a.event-loop[href]"):
        url = node.attributes.get("href") or ""
        if not url or url in cards:
            continue

        image = node.css_first(".img-wrap img")
        cards[url] = {
            "url": url,
            "title": _text(node, ".event-titles h3"),
            "subtitle": _text(node, ".event-titles h4"),
            "day": _text(node, ".event-date .day"),
            "month": _text(node, ".event-date .my"),
            "time": _text(node, ".event-time"),
            "hall": _text(node, ".hall-name"),
            "tags": _text(node, ".event-tags"),
            "image": (image.attributes.get("src") or "") if image else "",
        }
    return list(cards.values())


def parse_card(card: dict[str, str], s: ZoaTlvSettings, timezone: str) -> ScrapedEvent | None:
    """An archive card -> event (text and the seller's link come from its page); None without a date or title."""
    month = _MONTH_YEAR.search(card["month"])
    if not (month and card["day"].isdigit() and card["title"] and month.group(1) in MONTHS):
        return None
    clock = read_clock(card["time"])
    start = datetime(
        int(month.group(2)), MONTHS[month.group(1)], int(card["day"]),
        clock.hour if clock else 0, clock.minute if clock else 0, tzinfo=zone(timezone),
    )  # fmt: skip
    tags = [t for raw in card["tags"].split(",") if (t := raw.strip())]
    return ScrapedEvent(
        source=ZoaTlvScraper.name,
        source_event_id=unquote(urlsplit(card["url"]).path.rstrip("/").rsplit("/", 1)[-1]),
        url=card["url"],
        title=card["title"],
        description=card["subtitle"] or None,
        start_at=start,
        venue_name=s.venue_name,
        address=s.address,
        city=s.city,
        latitude=s.latitude,
        longitude=s.longitude,
        source_genres=tags,
        images=[card["image"]] if card["image"].startswith("http") else [],
        extra={"hall": card["hall"] or None, "subtitle": card["subtitle"] or None},
    )


def add_page(event: ScrapedEvent, page_html: str, seller_hosts: dict[str, str] | None = None) -> None:
    """The event's page: its text (after the subtitle), the full artwork and the seller's link."""
    tree = LexborHTMLParser(page_html)
    if (body := tree.css_first(".section-content .content")) and (text := html_to_text(body.html)):
        subtitle = (event.extra or {}).get("subtitle")
        event.description = f"{subtitle}\n\n{text}" if subtitle else text
    if (img := tree.css_first(".event-img img")) and (src := img.attributes.get("src") or "").startswith("http"):
        event.images = [src]
    link = tree.css_first("a.event-link[href]")
    href = (link.attributes.get("href") or "").strip() if link else ""
    host = urlsplit(href).hostname or ""
    if href.startswith("http") and host != urlsplit(str(event.url)).hostname:
        if seller := (seller_hosts or {}).get(host):
            href = urlsplit(href)._replace(netloc=seller).geturl()
        try:  # encoded as the model encodes Leaan's own URL (dedup compares URLs as text)
            event.extra = {**(event.extra or {}), "purchase_url": str(HttpUrl(href))}
        except ValueError:
            pass  # a malformed link is no seller; the event stands on its own page


def seller_tickets(page_html: str) -> list[TicketTier]:
    """The seller page's JSON-LD ``offers`` -> one tier per priced offer."""
    tiers = []
    for node in jsonld_nodes(page_html):
        offers = node.get("offers")
        for offer in offers if isinstance(offers, list) else [offers]:
            if not isinstance(offer, dict):
                continue
            sold_out = "soldout" in str(offer.get("availability") or "").lower().replace("_", "")
            for key in ("price", "lowPrice", "highPrice"):
                if (price := to_decimal(offer.get(key))) is not None:
                    tiers.append(TicketTier(
                        name=clean_str(offer.get("name")) or "כרטיס", price=price,
                        status=TierStatus.SOLD_OUT if sold_out else TierStatus.ON_SALE,
                    ))  # fmt: skip
    return list({(t.name, t.price): t for t in tiers}.values())
