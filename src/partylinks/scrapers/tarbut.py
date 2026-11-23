"""tarbut.events - a culture guide for Tel Aviv and the towns around it (Ramat Gan, Givatayim, Herzliya, Kiryat
Ono ...): theatre, music, dance, exhibitions, children's shows, lectures, copied from the venues' and box
offices' own sites (the CM Event Manager Pro plugin on WordPress).

The events page loads its list from ``admin-ajax.php`` (``action=vm_get_events_by_filters``, ``mode=event``,
``page=N``, ``append_only=1``): 10 date cards a page, nearest first (~430 pages, ~4,300 dates of ~2,700 events in
Nov 2026), each with the event's id and page. Pages are read a few at a time, and each event met for the first
time gets its own page (``/event/<slug>/``), which gives every coming date and time, each with its own ticket
link ("רכישת כרטיסים": Mevalim, Smarticket, the theatre's site; kept as the purchase link, so dedup ties it
to that seller's listing), the site's genres ("מחזה / דרמה", "פופ"), event type ("הצגה", "תערוכה") and age
group, the minimum price, the venue with its street address and map pin, a short text and the artwork (most
events have none: the plugin's ``no-event.png`` placeholder is not passed).

An exhibition's dates are runs ("28/08/2026 – 31/12/2026 : כל היום", one per week): kept once, at today (or its
first day, when that is ahead) until the run's last day, so it rolls forward instead of starting in the past.
The site's own categories are mapped in ``labels`` where ours read them otherwise ("קומדיה" here is a comedy
play beside a play's category, not stand-up). robots.txt allows every user agent but AI crawlers (Cloudflare's managed list).
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from html import unescape
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from pydantic import Field
from selectolax.lexbor import LexborHTMLParser, LexborNode

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import clean_str, html_to_text, to_float, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_DAY = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")
_CLOCK = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_PRICE = re.compile(r"מחיר מינימום לכרטיס:\s*₪?\s*(\d+(?:\.\d+)?)")
_CARD = re.compile(r'data-event-id="(\d+)"')


class TarbutSettings(SourceSettings):
    site_url: str = "https://www.tarbut.events"
    ajax_path: str = "/wp-admin/admin-ajax.php"
    listing_form: dict[str, str] = Field(
        default_factory=lambda: {"action": "vm_get_events_by_filters", "mode": "event", "id": "0", "append_only": "1"}
    )
    pages_at_once: int = 4
    max_pages: int = 1500
    placeholder_image: str = "no-event.png"
    # The site's region name -> the city, for a venue whose address names none ("סביב תל אביב" is no city).
    region_cities: dict[str, str] = Field(default_factory=dict)
    # A site category -> the labels declared instead (ours read some names otherwise).
    labels: dict[str, list[str]] = Field(default_factory=dict)
    # An all-day run of weeks is an exhibition whatever its type says (museums' shows are typed "הופעה").
    run_types: dict[str, str] = Field(default_factory=lambda: {"הופעה": "תערוכה"})
    # Beside a play's category, the site's "קומדיה" is a comedy play (ours read it as stand-up).
    play_categories: list[str] = Field(
        default_factory=lambda: ["מחזה / דרמה", "הצגה", "מחזה לילדים", "הצגה לילדים", "מחזמר"]
    )
    play_labels: dict[str, str] = Field(default_factory=lambda: {"קומדיה": "מחזה קומי"})
    free_badge: str = "חינמי"  # the title's badge "אירוע חינמי"
    free_ticket_name: str = "כניסה חופשית"


class TarbutScraper(BaseScraper[TarbutSettings]):
    name = "tarbut"
    settings_model = TarbutSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        tz = zone(self.timezone)
        seen: set[str] = set()
        first = 1
        while first <= s.max_pages:
            numbers = range(first, min(first + s.pages_at_once, s.max_pages + 1))
            pages = await asyncio.gather(*(self._page(n) for n in numbers))
            todo = []
            for page in pages:  # in page order: nearest first
                for event_id, url in parse_listing(page):
                    if event_id not in seen:
                        seen.add(event_id)
                        todo.append((event_id, url))
            today = datetime.now(tz).date()

            async def detail(item: tuple[str, str], today: date = today) -> list[ScrapedEvent]:
                return parse_event_page(await self.get_text(item[1]), item[0], item[1], s, self.timezone, today)

            events = [e async for batch in self.stream_isolated(todo, detail, lambda i: f"event {i[0]}") for e in batch]
            for event in sorted(
                events, key=lambda e: (e.start_at or datetime.max.replace(tzinfo=tz), e.source_event_id)
            ):
                if is_current(
                    event.start_at, event.end_at, RetentionSettings(), self.timezone, time_known=event.time_known
                ):
                    yield event
            if any(not _CARD.search(p) for p in pages):
                return  # the list ran out
            first += s.pages_at_once
        self.log.warning("listing truncated at max_pages=%d", s.max_pages)

    async def _page(self, number: int) -> str:
        s = self.settings
        response = await self.request("POST", s.site_url + s.ajax_path, data={**s.listing_form, "page": str(number)})
        return response.text


def parse_listing(page_html: str) -> list[tuple[str, str]]:
    """One listing page -> (event id, event page) per date card, in the page's order."""
    tree = LexborHTMLParser(f"<div>{page_html}</div>")
    out = []
    for card in tree.css("div.event[data-event-id]"):
        link = card.css_first(".event-title a[href]")
        if link is not None and (href := link.attributes.get("href") or "").startswith(("http://", "https://")):
            out.append((str(card.attributes.get("data-event-id")), href))
    return out


def parse_event_page(
    page_html: str, event_id: str, url: str, s: TarbutSettings, timezone: str, today: date
) -> list[ScrapedEvent]:
    """An event's page -> one event per coming date and time (a run once)."""
    tree = LexborHTMLParser(page_html)
    hero = tree.css_first(".vp-hero-body")
    title_node = tree.css_first("h1.vp-title")
    badges = []
    if title_node is not None:  # the badge after the name ("אירוע חינמי") is not part of it
        for badge in title_node.css("span"):
            badges.append(clean_str(badge.text()) or "")
            badge.decompose()
    title = clean_str(unescape(title_node.text())) if title_node else None
    if title and not any(c.isalpha() for c in title):
        return []  # a number for a name ("6605110"): the site's placeholder, no event
    free = any(s.free_badge in b for b in badges)
    if hero is None or not title:
        return []
    tz = zone(timezone)
    region = _link_text(hero, "city_id=")
    categories = [*_links_text(hero, "genre_id="), *_links_text(hero, "eventtype_id=")]

    play = any(c in s.play_categories for c in categories)

    def genres(run: bool) -> list[str]:
        out: list[str] = []
        for category in categories:
            name = s.run_types.get(category, category) if run else category
            if name in s.play_labels and play:
                name = s.play_labels[name]
            out += s.labels.get(name, [name])
        return list(dict.fromkeys(g.lower() for g in out))

    ages = _links_text(hero, "agegroup")
    venue, address, latitude, longitude = _venue(tree)
    venue = venue or _link_text(hero, "/venue/")
    city = _city(address, region, s)
    price_match = _PRICE.search(hero.text(separator=" "))
    price = Decimal(price_match.group(1)) if price_match else None
    main_cta = tree.css_first(".vp-cta a.vp-btn-external[href]")
    fallback_link = _seller(main_cta.attributes.get("href") if main_cta else None, s)
    short = tree.css_first(".vp-short-description")
    description = html_to_text(short.html) if short is not None else None
    image = tree.css_first(".event-featured-image img")
    src = (image.attributes.get("src") or "") if image is not None else ""
    images = (
        [quote(src, safe=":/%?=&")]
        if src.startswith(("http://", "https://")) and s.placeholder_image not in src
        else []
    )

    def event(key: str, start: datetime, end: datetime | None, time_known: bool, link: str | None) -> ScrapedEvent:
        extra: dict[str, Any] = {"ages": ages} if ages else {}
        if link or fallback_link:
            extra["purchase_url"] = link or fallback_link
        if region:
            extra["region"] = region
        return ScrapedEvent(
            source=TarbutScraper.name,
            source_event_id=f"{event_id}:{key}",
            url=url,
            title=title,
            description=description,
            start_at=start,
            end_at=end,
            time_known=time_known,
            venue_name=venue,
            address=address,
            city=city,
            latitude=latitude,
            longitude=longitude,
            source_genres=genres(key == "run"),
            images=images,
            min_price=Decimal(0) if free else price,
            tickets=[TicketTier(name=s.free_ticket_name, price=Decimal(0), is_free=True)] if free else [],
            extra=extra,
        )

    out: dict[str, ScrapedEvent] = {}
    run_start: date | None = None
    run_end: date | None = None
    for line in hero.css("p"):
        text = line.text(separator=" ")
        days = [date(int(y), int(m), int(d)) for d, m, y in _DAY.findall(text)]
        if not days:
            continue
        link = next((_seller(a.attributes.get("href"), s) for a in line.css("a.vp-btn-external[href]")), None)
        if len(days) > 1 and days[-1] > days[0]:  # a run: kept once, below
            run_start = min(run_start or days[0], days[0])
            run_end = max(run_end or days[-1], days[-1])
            continue
        clock = _CLOCK.search(text)  # "29/11/2026 : 20:30"; "כל היום" (all day) has none
        start = datetime.combine(
            days[0], time(int(clock.group(1)), int(clock.group(2))) if clock else time.min, tzinfo=tz
        )
        out.setdefault(f"{start:%Y%m%d%H%M}", event(f"{start:%Y%m%d%H%M}", start, None, bool(clock), link))
    if run_start and run_end and run_end >= today:
        first = max(run_start, today)
        start = datetime.combine(first, time.min, tzinfo=tz)
        end = datetime.combine(run_end, time.min, tzinfo=tz) + timedelta(days=1, minutes=-1)
        run = event("run", start, end, False, None)
        run.extra["run"] = [run_start.isoformat(), run_end.isoformat()]
        out["run"] = run
    return list(out.values())


def _links_text(node: LexborNode, href_part: str) -> list[str]:
    out = []
    for link in node.css("a[href]"):
        if href_part in (link.attributes.get("href") or "") and (text := clean_str(unescape(link.text()))):
            out.append(text)
    return list(dict.fromkeys(out))


def _link_text(node: LexborNode, href_part: str) -> str | None:
    return next(iter(_links_text(node, href_part)), None)


def _venue(tree: LexborHTMLParser) -> tuple[str | None, str | None, float | None, float | None]:
    """The sidebar's venue: name (without its type), street address and the map link's pin."""
    side = tree.css_first(".vp-side")
    if side is None:
        return None, None, None, None
    name = None
    if (title := side.css_first(".venue-page-link__title")) is not None:
        for kind in title.css(".venue-page-link__type"):
            kind.decompose()
        name = clean_str(unescape(title.text()))
    address = latitude = longitude = None
    for item in side.css(".vp-kv .item"):
        key = item.css_first("p.k")
        link = item.css_first("a[href]")
        if key is None or link is None or clean_str(key.text()) != "כתובת":
            continue
        address = clean_str(unescape(link.text()))
        point = (parse_qs(urlparse(link.attributes.get("href") or "").query).get("ll") or [""])[0].split(",")
        if len(point) == 2:
            latitude, longitude = to_float(point[0]), to_float(point[1])
        if not latitude or not longitude:
            latitude = longitude = None
    return name, address, latitude, longitude


def _city(address: str | None, region: str | None, s: TarbutSettings) -> str | None:
    """The address's last part ("הוברמן 1, תל אביב-יפו"), unless it holds a number (a street alone); else the
    region when it is one city."""
    last = clean_str(address.rsplit(",", 1)[-1]) if address else None
    if last and not any(c.isdigit() for c in last):  # "רמת גן" alone is the city too
        return last
    return s.region_cities.get(region or "")


def _seller(href: str | None, s: TarbutSettings) -> str | None:
    """A ticket link off the site (an internal link is no seller)."""
    if not href or not href.startswith(("http://", "https://")):
        return None
    host = urlparse(href).netloc.lower().removeprefix("www.")
    return None if host == urlparse(s.site_url).netloc.lower().removeprefix("www.") else href
