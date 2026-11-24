"""toMix (to-mix.co.il) - a WooCommerce shop of shows and experiences (its own productions, the tox and
UpTown clubs, Batsheva's Anafaza, touring musicals) and of vouchers (spa, food, tours) that are no events.

Every product, from every place the site lists one:

* every product category (``product_cat-sitemap.xml``: ~200, from "במה ומופעים" to each artist's page under
  ``talents``), every page of each (``?page=N``, 12 cards a page, to the first page with nothing new; the
  "load more" button calls ``/wp-admin/admin-ajax.php``, which robots.txt disallows, as it does ``/wp-json``);
* the HTML site map (``/sitemap/``: ~540 product links, a third of them in no category) and every page of
  the page / post / specialty sitemaps (landing pages that link products of their own).

A product page whose buy button is ``?eventbuzz=true`` is a show; the others are dated nowhere (vouchers).
The ``?eventbuzz=true`` page holds the show's box office, one of:

* an Eventer event (iframe ``eventer.co.il/<linkName>``) or a table of dates with one Eventer link each;
* an Eventer profile filtered by tag (``eventer.co.il/user/<user>?tag=<tag>``): ``/user/<user>/getData``
  lists every event of the profile with its tags, schedule, place and tickets (one request per profile;
  ``eventer_users`` are always read, so a toMix event no product shows is not missed). A linked event of
  another profile: ``/events/explainNames/<linkName>.js``;
* toMix's BiggerPicture shop (``tickets.to-mix.co.il/site/886?code=886-<event>``), or its event pages on
  ``app.to-mix.co.il`` (``/event/adminclientredirect/?emcode=<event master code>``). Every event master of
  the site is read (ids 1, 2, 3... to ``master_gap`` misses past the last one; the app site's
  ``mb_event-sitemap.xml`` names their pages): ``/cus/eventMaster/<id>/eventsDays`` (a year) gives the
  dates, ``/cus/eventMaster/<id>/site/<site>/startDate/../endDate/..`` (60 days at most) the performances
  (time, hall), ``/cus/event/code/<site>-<event>/min`` the prices.

A show's page gives its title, picture, text and categories (the sections it is listed in; an artist's
section names the artist). Halls of the BiggerPicture shop are placed through ``venues`` in config; Eventer
events carry their own place and coordinates. The seller's page is kept as the purchase link, so Eventer's
own listing of the same event merges with it.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlsplit

from pydantic import BaseModel, Field
from selectolax.lexbor import LexborHTMLParser

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import ParseError, clean_str, html_to_text, og_image, parse_iso, to_int, zone
from partylinks.retention import is_current
from partylinks.scrapers import eventer
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.biggerpicture import parse_event_tiers

_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_PRODUCT = re.compile(r"https://www\.to-mix\.co\.il/product/[^\"'\s<>#?]+")
_EVENTER = re.compile(r"https://www\.eventer\.co\.il/([^\"'\s<>?#]+)(\?[^\"'\s<>#]*)?")
_SHOP = re.compile(r"https://tickets\.to-mix\.co\.il/site/(\d+)\?[^\"'\s<>]*?code=(\d+)-(\d+)")
_APP_CODE = re.compile(r"app\.to-mix\.co\.il/event/adminclientredirect/\?emcode=(\d+)")
_EM_ID = re.compile(r"data-em-id='(\d+)'")
_BANNER = re.compile(r'class="master-event__banner-image"\s+src="([^"]+)"')


class Hall(BaseModel):
    """A hall of the BiggerPicture shop: where it is (the shop names it only)."""

    venue_name: str
    address: str | None = None
    city: str
    latitude: float | None = None
    longitude: float | None = None


class TomixSettings(SourceSettings):
    site_url: str = "https://www.to-mix.co.il"
    categories_sitemap: str = "/product_cat-sitemap.xml"
    # Pages that link products outside the categories: the HTML site map, and every page of these sitemaps.
    link_pages: list[str] = Field(default_factory=lambda: ["/", "/sitemap/"])
    link_sitemaps: list[str] = Field(
        default_factory=lambda: ["/page-sitemap.xml", "/post-sitemap.xml", "/specialty-sitemap.xml"]
    )
    page_query: str = "?page={page}"
    max_pages: int = 500  # a runaway guard only: a category ends at its first page with nothing new
    more_marker: str = 'data-has-more-mix="1"'
    checkout_query: str = "?eventbuzz=true"
    talents_path: str = "/talents/"  # an artist's section: names the artist, not a genre
    category_root: str = "/product-category/"
    # Sections that are offers, not a kind of show (club benefits, holidays, gifts, "all experiences"): their
    # names are not passed as categories (the show's own sections are).
    plain_sections: list[str] = Field(default_factory=list)
    default_labels: list[str] = Field(default_factory=lambda: ["במה ומופעים"])  # shows no product lists
    # Eventer
    eventer_url: str = "https://www.eventer.co.il"
    eventer_users: list[str] = Field(default_factory=lambda: ["tomix", "mixcard"])
    eventer_user_path: str = "/user/{user}/getData"
    eventer_details_path: str = "/events/explainNames/{link_name}.js"
    # BiggerPicture
    shop_api: str = "https://pub-api-use1.biggerpicture.ai/ecomAPI/public/api"
    shop_site: int = 886
    shop_channel: str = "web"
    shop_language: str = "he"
    shop_page: str = "https://tickets.to-mix.co.il/site/{site}?saleChannelCode=web&code={site}-{event}"
    master_gap: int = 20
    days_ahead: int = 365  # eventsDays' longest range
    window_days: int = 59  # the performances call answers at most 60 days
    app_url: str = "https://app.to-mix.co.il"
    app_sitemap: str = "/mb_event-sitemap.xml"
    venues: dict[str, Hall] = Field(default_factory=dict)
    # Eventer places with no town (the address names a kibbutz or a site): a words-in-the-address -> hall map.
    places: dict[str, Hall] = Field(default_factory=dict)
    # Event masters sold to one company (the shop's ticket name says so): closed events.
    private_words: list[str] = Field(default_factory=lambda: ["חברת הפקה"])


@dataclass
class Product:
    url: str
    title: str
    image: str | None
    description: str | None
    labels: list[str] = field(default_factory=list)
    artists: list[str] = field(default_factory=list)
    eventer_links: list[str] = field(default_factory=list)
    eventer_tags: list[tuple[str, str]] = field(default_factory=list)
    shop_events: list[int] = field(default_factory=list)
    master_codes: list[str] = field(default_factory=list)


@dataclass
class Checkout:
    eventer_links: list[str] = field(default_factory=list)
    eventer_tags: list[tuple[str, str]] = field(default_factory=list)
    shop_events: list[int] = field(default_factory=list)
    master_codes: list[str] = field(default_factory=list)


def product_key(url: str) -> str:
    """One spelling per product: decoded, no query, a trailing slash."""
    path = unquote(urlsplit(url).path).rstrip("/") + "/"
    return "https://www.to-mix.co.il" + path


def page_url(key: str) -> str:
    return quote(key, safe=":/")


def sitemap_locs(xml: str) -> list[str]:
    return list(dict.fromkeys(_LOC.findall(xml)))


def product_links(html: str) -> list[str]:
    return list(dict.fromkeys(product_key(u) for u in _PRODUCT.findall(html)))


def parse_category_page(html: str, more_marker: str = 'data-has-more-mix="1"') -> tuple[str | None, list[str], bool]:
    """A category page -> (its name, the products of its grid, whether it has more pages)."""
    tree = LexborHTMLParser(html)
    title = tree.css_first('meta[property="og:title"]')
    name = clean_str(title.attributes.get("content")) if title else None
    if name:
        name = re.split(r"\s+[-|]\s+toMix", name)[0].strip()
    grid = tree.css_first("#wl_ajax_container")
    cards = grid.css(".boxitem[data-url]") if grid else []
    products = list(dict.fromkeys(product_key(str(c.attributes.get("data-url"))) for c in cards))
    return name, products, more_marker in html


def parse_product(html: str, url: str) -> Product | None:
    """A product page -> the show; None for a voucher (no dated box office behind its buy button)."""
    tree = LexborHTMLParser(html)
    button = tree.css_first('.btnholder a[href*="eventbuzz=true"]')
    if button is None:
        return None
    heading = tree.css_first("#perfect_for h2")
    title = clean_str(heading.text()) if heading else None
    if not title:
        og = tree.css_first('meta[property="og:title"]')
        title = clean_str(og.attributes.get("content")) if og else None
    if not title:
        raise ParseError("product without a title")
    info = tree.css_first(".mix-maininfo-content")
    description = html_to_text(info.html) if info and info.html else None
    if description:
        description = re.sub(r"^\s*For English\s*", "", description)
    return Product(url=url, title=title, image=og_image(html), description=description)


def parse_checkout(html: str) -> Checkout:
    """The ``?eventbuzz=true`` page's box office: Eventer events / profile tags, shop events, master codes."""
    start, end = html.find("IFRAME CHECKOUT START"), html.find("IFRAME CHECKOUT END")
    part = html[start:end] if 0 <= start < end else ""
    out = Checkout()
    for path, query in _EVENTER.findall(part):
        parts = [p for p in path.split("/") if p]
        if len(parts) == 2 and parts[0] == "user":
            tags = parse_qs((query or "?").lstrip("?")).get("tag") or [""]
            out.eventer_tags.append((parts[1], tags[0]))
        elif len(parts) == 1 and not parts[0].endswith(".js"):
            out.eventer_links.append(parts[0])
    out.shop_events = [int(event) for _site, _code, event in _SHOP.findall(part)]
    out.master_codes = _APP_CODE.findall(part)
    out.eventer_links = list(dict.fromkeys(out.eventer_links))
    out.eventer_tags = list(dict.fromkeys(out.eventer_tags))
    return out


def eventer_event(
    details: dict[str, Any], tickets: dict[str, Any], product: Product | None, s: TomixSettings, timezone: str
) -> ScrapedEvent:
    """An Eventer event sold by toMix, with the product's page, picture, text and categories when one sells it."""
    raw = details.get("event") or {}
    page = f"{s.eventer_url}/{raw.get('linkName')}"
    base = eventer.parse_event(details, tickets, page, timezone)
    update: dict[str, Any] = {
        "source": TomixScraper.name,
        "source_event_id": f"eventer-{base.source_event_id}",
        "extra": {**base.extra, "purchase_url": page},
    }
    if product is not None:
        update |= {
            "url": page_url(product.url),
            "source_genres": [label.lower() for label in product.labels],
            "lineup": product.artists,
            "images": [i for i in [product.image, *base.images] if i],
            "description": product.description or base.description,
        }
    else:
        update["source_genres"] = [label.lower() for label in s.default_labels]
    if not base.city and (place := next((h for k, h in s.places.items() if k in (base.address or "")), None)):
        update |= {"venue_name": place.venue_name, "city": place.city}
        if place.latitude is not None and base.latitude is None:
            update |= {"latitude": place.latitude, "longitude": place.longitude}
    return base.model_copy(update=update)


def shop_event(
    row: dict[str, Any],
    master: dict[str, Any],
    tiers: list[TicketTier],
    product: Product | None,
    app_page: tuple[str, str | None] | None,
    s: TomixSettings,
    timezone: str,
) -> ScrapedEvent | None:
    """One performance of the BiggerPicture shop."""
    event_id = to_int(row.get("eventId"))
    start = parse_iso(row.get("dateTimeOfEvent"), timezone)
    title = clean_str(master.get("displayName")) or clean_str(row.get("eventMasterName"))
    if event_id is None or start is None or not title:
        return None
    length = to_int(row.get("lengthInMinutes"))
    hall_name = clean_str(row.get("venueName")) or ""
    hall = s.venues.get(re.sub(r"\s+", " ", hall_name))
    shop = s.shop_page.format(site=s.shop_site, event=event_id)
    url = page_url(product.url) if product else (app_page[0] if app_page else shop)
    images = [i for i in [product.image if product else None, app_page[1] if app_page else None] if i]
    priced = [t.price for t in tiers if t.price is not None]
    return ScrapedEvent(
        source=TomixScraper.name,
        source_event_id=f"shop-{s.shop_site}-{event_id}",
        url=url,
        title=product.title if product else title,
        description=product.description if product else None,
        start_at=start,
        end_at=start + timedelta(minutes=length) if length and 0 < length <= 600 else None,
        venue_name=hall.venue_name if hall else hall_name or None,
        address=(hall.address if hall else None) or (hall.venue_name if hall else hall_name or None),
        city=hall.city if hall else None,
        latitude=hall.latitude if hall else None,
        longitude=hall.longitude if hall else None,
        source_genres=[label.lower() for label in (product.labels if product else s.default_labels)],
        lineup=product.artists if product else [],
        images=images,
        organizer="toMix",
        tickets=tiers,
        min_price=min(priced, default=None),
        max_price=max(priced, default=None),
        extra={"purchase_url": shop, "event_master": to_int(master.get("eventMasterId"))},
    )


class TomixScraper(BaseScraper[TomixSettings]):
    name = "tomix"
    settings_model = TomixSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        labels = await self._categories()
        keys = list(labels)
        for found in await asyncio.gather(*(self._linked(p) for p in s.link_pages), self._from_sitemaps()):
            keys += found
        keys = list(dict.fromkeys(keys))
        self.log.debug("%d products (%d in categories)", len(keys), len(labels))
        products: list[Product] = []
        async for product in self.stream_isolated(keys, self._product, label=lambda k: k):
            sections = [(n, path) for n, path in labels.get(product.url, []) if not self._plain(path)]
            product.labels = [n for n, path in sections if s.talents_path not in path] or list(s.default_labels)
            product.artists = [n for n, path in sections if s.talents_path in path]
            products.append(product)
        products.sort(key=lambda p: p.url)
        self.log.debug("%d shows", len(products))

        events: list[ScrapedEvent] = []
        events += await self._eventer(products)
        events += await self._shop(products)
        retention = RetentionSettings()
        current = [
            e for e in events if is_current(e.start_at, e.end_at, retention, self.timezone, time_known=e.time_known)
        ]
        far = datetime.max.replace(tzinfo=zone(self.timezone))
        for event in sorted(current, key=lambda e: e.start_at or far):
            yield event

    def _plain(self, path: str) -> bool:
        s = self.settings
        section = path.removeprefix(s.category_root).strip("/").split("/")[0]
        return section in s.plain_sections

    # ------------------------------------------------------------------ products
    async def _categories(self) -> dict[str, list[tuple[str, str]]]:
        """Product -> the (name, path) of every category listing it."""
        s = self.settings
        cats = sitemap_locs(await self.get_text(s.site_url + s.categories_sitemap))
        if not cats:
            raise ParseError("no product categories")
        out: dict[str, list[tuple[str, str]]] = {}
        async for name, path, products in self.stream_isolated(cats, self._category, label=lambda c: c):
            for key in products:
                out.setdefault(key, []).append((name, path))
        return out

    async def _category(self, url: str) -> tuple[str, str, list[str]]:
        s = self.settings
        path = unquote(urlsplit(url).path)
        name: str | None = None
        seen: dict[str, None] = {}
        for page in range(1, s.max_pages + 1):
            html = await self.get_text(url if page == 1 else url + s.page_query.format(page=page))
            title, products, more = parse_category_page(html, s.more_marker)
            name = name or title
            new = [p for p in products if p not in seen]
            seen.update(dict.fromkeys(new))
            if not new or not more:
                break
        label = name or path.rstrip("/").rsplit("/", 1)[-1]
        return label, path, list(seen)

    async def _linked(self, path: str) -> list[str]:
        html = await self.optional(f"page {path}", self.get_text(self.settings.site_url + path))
        return product_links(html or "")

    async def _from_sitemaps(self) -> list[str]:
        s = self.settings
        pages: list[str] = []
        for sitemap in s.link_sitemaps:
            pages += sitemap_locs(await self.optional(f"sitemap {sitemap}", self.get_text(s.site_url + sitemap)) or "")
        found: list[str] = []
        async for links in self.stream_isolated(pages, lambda u: self._page_links(u), label=lambda u: u):
            found += links
        return found

    async def _page_links(self, url: str) -> list[str]:
        return product_links(await self.get_text(url))

    async def _product(self, key: str) -> Product | None:
        url = page_url(key)
        product = parse_product(await self.get_text(url), key)
        if product is None:
            return None
        checkout = parse_checkout(await self.get_text(url + self.settings.checkout_query))
        product.eventer_links, product.eventer_tags = checkout.eventer_links, checkout.eventer_tags
        product.shop_events, product.master_codes = checkout.shop_events, checkout.master_codes
        return product

    # ------------------------------------------------------------------ Eventer
    async def _eventer(self, products: list[Product]) -> list[ScrapedEvent]:
        s = self.settings
        users = list(dict.fromkeys([*s.eventer_users, *(u for p in products for u, _ in p.eventer_tags)]))
        listed: dict[str, dict[str, Any]] = {}
        for user in users:
            payload = await self.optional(
                f"eventer user {user}",
                self.get_json(s.eventer_url + s.eventer_user_path.format(user=user), expect=dict | str),
            )
            for raw in (payload.get("events") or []) if isinstance(payload, dict) else []:
                if isinstance(raw, dict) and raw.get("linkName"):
                    listed.setdefault(str(raw["linkName"]), {**raw, "_user": user})
        by_link: dict[str, Product] = {}
        by_tag: dict[tuple[str, str], Product] = {}
        for p in products:
            for link in p.eventer_links:
                by_link.setdefault(link, p)
            for tag in p.eventer_tags:
                by_tag.setdefault(tag, p)

        def owner(link: str, raw: dict[str, Any]) -> Product | None:
            if link in by_link:
                return by_link[link]
            return next((by_tag[(raw["_user"], t)] for t in raw.get("tags") or [] if (raw["_user"], t) in by_tag), None)

        events: list[ScrapedEvent] = []
        for link, raw in listed.items():
            try:
                tickets = {"ticketTypes": raw.get("ticketTypes") or [], "totalRemaining": raw.get("totalRemaining")}
                events.append(eventer_event({"event": raw}, tickets, owner(link, raw), s, self.timezone))
            except Exception as exc:  # noqa: BLE001 - one odd event never drops the others
                self.record_failure(f"eventer {link}", exc)
        others = [link for link in by_link if link not in listed]
        async for event in self.stream_isolated(
            others, lambda n: self._eventer_details(n, by_link[n]), label=lambda n: n
        ):
            events.append(event)
        return events

    async def _eventer_details(self, link: str, product: Product) -> ScrapedEvent | None:
        s = self.settings
        details = await self.get_json(s.eventer_url + s.eventer_details_path.format(link_name=link), expect=dict)
        if not isinstance(details.get("event"), dict) or not details["event"].get("_id"):
            return None
        return eventer_event(details, {}, product, s, self.timezone)

    # ------------------------------------------------------------------ BiggerPicture
    async def _shop(self, products: list[Product]) -> list[ScrapedEvent]:
        s = self.settings
        token = await self._login()
        headers = {"Authorization": f"Bearer {token}"}
        app_pages = await self.optional("app pages", self._app_pages()) or {}
        masters: dict[int, dict[str, Any]] = {}
        em, misses, last = 1, 0, max(app_pages, default=0)
        while misses < s.master_gap or em <= last:
            # No such master: an empty 200 (past the last id, or a deleted one).
            body = await self.optional(
                f"master {em}", self.get_text(f"{s.shop_api}/cus/eventMaster/{em}", headers=headers)
            )
            master = json.loads(body) if body and body.strip().startswith("{") else None
            if isinstance(master, dict) and master.get("eventMasterId"):
                misses = 0
                ticket = str(master.get("ticketName") or "")
                if not any(w in ticket for w in s.private_words):
                    masters[em] = master
            else:
                misses += 1
            em += 1
        self.log.debug("%d event masters", len(masters))
        by_event = {e: p for p in products for e in p.shop_events}
        by_code = {c: p for p in products for c in p.master_codes}
        rows = []
        async for found in self.stream_isolated(
            masters, lambda m: self._performances(m, headers), label=lambda m: f"master {m}"
        ):
            rows += found
        events: list[ScrapedEvent] = []

        async def one(row: dict[str, Any]) -> ScrapedEvent | None:
            master = masters[int(row["eventMasterId"])]
            event_id = to_int(row.get("eventId"))
            product = by_event.get(event_id or -1) or by_code.get(str(master.get("code")))
            if product is None:  # another performance of a product's show
                product = next((by_event[e] for e in by_event if e in master.get("_events", ())), None)
            tiers = await self.optional(f"prices {event_id}", self._tiers(event_id, headers)) or []
            return shop_event(row, master, tiers, product, app_pages.get(int(row["eventMasterId"])), s, self.timezone)

        for master_id, master in masters.items():
            master["_events"] = {to_int(r.get("eventId")) for r in rows if to_int(r.get("eventMasterId")) == master_id}
        async for event in self.stream_isolated(rows, one, label=lambda r: f"shop event {r.get('eventId')}"):
            events.append(event)
        return events

    async def _login(self) -> str:
        s = self.settings
        body = {"siteId": s.shop_site, "saleChannelCode": s.shop_channel, "language": s.shop_language}
        payload = await self.post_json(s.shop_api + "/sys/login", json=body, expect=dict)
        if not (token := clean_str(payload.get("token"))):
            raise ParseError("shop login without token")
        return token

    async def _performances(self, master_id: int, headers: dict[str, str]) -> list[dict[str, Any]]:
        s = self.settings
        today = datetime.now(zone(self.timezone)).date()
        path = f"{s.shop_api}/cus/eventMaster/{master_id}/eventsDays/startDate/{today}/endDate/{today + timedelta(days=s.days_ahead - 1)}"
        days = (await self.get_json(path, headers=headers, expect=dict)).get("list") or []
        dates = sorted({d for x in days if isinstance(x, dict) and (d := _day(x.get("businessDate")))})
        rows: list[dict[str, Any]] = []
        for first, last in windows(dates, s.window_days):
            url = f"{s.shop_api}/cus/eventMaster/{master_id}/site/{s.shop_site}/startDate/{first}/endDate/{last}"
            payload = await self.get_json(url, headers=headers, expect=dict)
            rows += [r for r in payload.get("list") or [] if isinstance(r, dict) and r.get("isActive", True)]
        return rows

    async def _tiers(self, event_id: int | None, headers: dict[str, str]) -> list[TicketTier]:
        s = self.settings
        if event_id is None:
            return []
        url = f"{s.shop_api}/cus/event/code/{s.shop_site}-{event_id}/min"
        return parse_event_tiers(await self.get_json(url, headers=headers, expect=dict))

    async def _app_pages(self) -> dict[int, tuple[str, str | None]]:
        """Event master id -> (its page on app.to-mix.co.il, its banner)."""
        s = self.settings
        pages = sitemap_locs(await self.get_text(s.app_url + s.app_sitemap))
        out: dict[int, tuple[str, str | None]] = {}

        async def one(url: str) -> tuple[int, str, str | None] | None:
            html = await self.get_text(url)
            if not (em := _EM_ID.search(html)):
                return None
            banner = _BANNER.search(html)
            return int(em.group(1)), url, banner.group(1) if banner else None

        async for em, url, banner in self.stream_isolated(pages, one, label=lambda u: u):
            out.setdefault(em, (url, banner))
        return out


def _day(value: Any) -> date | None:
    moment = parse_iso(value)
    return moment.date() if moment else None


def windows(dates: Iterable[date], span: int) -> list[tuple[date, date]]:
    """Sorted dates -> the fewest (first, last) ranges of at most ``span`` + 1 days covering them."""
    out: list[tuple[date, date]] = []
    for day in sorted(dates):
        if out and (day - out[-1][0]).days <= span:
            out[-1] = (out[-1][0], day)
        else:
            out.append((day, day))
    return out
