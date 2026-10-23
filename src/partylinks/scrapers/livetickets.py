"""LiveTickets (livetickets.co.il) - tickets to concerts, sports and festivals abroad.

ASP.NET site with no API or sitemap (``/sitemap.xml`` is a 404). Listing pages are crawled
in two hops: the home page and the category pages it links (``/Category1-3/...``) link every
performer page (``/performers/``, ``/DT_performers/``) and the city and venue pages (~600).
Each of those lists its events 15 a page, next pages by the "next page" link (``PageNum=``).
A row gives the event's date and local time (Hebrew month), name, venue, Hebrew city and
country, the "from" price with its currency sign and the event id (``events.aspx?eid=``).
No detail requests: the event page adds only the seat categories. ~700 requests per run.
robots.txt disallows only ``/bin/``, ``/nrereco/`` and ``/search/`` (the "all events" links).
"""

from __future__ import annotations

import html as htmllib
import re
from collections.abc import AsyncIterator
from datetime import datetime
from urllib.parse import unquote, urljoin

from pydantic import Field

from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, to_decimal, zone
from partylinks.scrapers.abroad import country_code, local_zone
from partylinks.scrapers.base import BaseScraper, SourceSettings, gather_or_cancel

HEBREW_MONTHS = {
    m: i
    for i, m in enumerate(
        ["ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני", "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר"], 1
    )
}
CURRENCY_SIGNS = {"€": "EUR", "£": "GBP", "$": "USD", "₪": "ILS"}

_HREF = re.compile(r'href="([^"]+)"')
# Pages that list events (performer, city, venue) or link them (category).
_LISTING = re.compile(r"/(?:category[123]|performers|dt_performers|cities|venues)/[^/?#]+\.aspx$", re.IGNORECASE)
_ROW = re.compile(r'<li class="list_tickets_item">(.*?)</li>', re.DOTALL)
_YEAR = re.compile(r'tickets_date_year">\s*(\d{4})')
_DAY = re.compile(r'tickets_date_day">\s*(\d{1,2})\s+([^\s<]+)')
_TIME = re.compile(r"(\d{1,2}):(\d{2})")
_NAME = re.compile(r'tickets_name_txt">(.*?)</div>', re.DOTALL)
_PLACE = re.compile(r'tickets_place_txt[^"]*">(.*?)</div>', re.DOTALL)
_EID = re.compile(r"events\.aspx\?eid=(\d+)")
_PRICE = re.compile(r'tickets_price_currency">([^<]*)</div>\s*<div>([^<]*)</div>')
_NEXT = re.compile(r'id="hplNext"[^>]*href="([^"]+)"')
# The top menu links popular performers of every kind from every page: not the category's own.
_MENU_LINK = re.compile(r'<a\b[^>]*class="[^"]*navlink[^"]*"[^>]*>', re.IGNORECASE)


class LiveTicketsSettings(SourceSettings):
    site_url: str
    event_path: str  # must contain "{eid}"
    max_pages: int = Field(30, ge=1)  # pages per listing


class LiveTicketsScraper(BaseScraper[LiveTicketsSettings]):
    name = "livetickets"
    settings_model = LiveTicketsSettings
    abroad_only = True

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        listings = await self._listings()
        self.log.info("crawling %d listing pages", len(listings))
        # An event is on its performer's, city's and venue's pages; only some of those hang under a
        # category page, so categories merge across pages before the event goes out.
        # ponytail: events are held until the crawl ends (~2 min); stream per page if runs get long.
        events: dict[str, ScrapedEvent] = {}
        async for page_events in self.stream_isolated(list(listings), self._listing_events, label=lambda u: u):
            for event in page_events:
                kept = events.setdefault(event.source_event_id, event)
                kept.source_genres = list(dict.fromkeys([*kept.source_genres, *listings[event.extra["listing"]]]))
        for event in events.values():
            event.extra.pop("listing")
            yield event

    async def _listings(self) -> dict[str, list[str]]:
        """Performer, city and venue pages linked from the home page and its category pages; a performer
        page carries the categories of the category pages that link it ("concerts", "soccer"...)."""
        home = self.settings.site_url + "/"
        found = {k: (u, []) for k, u in listing_links(await self.get_text(home), home).items()}
        categories = [u for u, _ in found.values() if "/category" in u.lower()]
        for url, page in zip(categories, await self._pages(categories), strict=True):
            category = category_name(url)
            for key, link in listing_links(_MENU_LINK.sub("", page), self.settings.site_url + "/").items():
                entry = found.setdefault(key, (link, []))
                if "performers/" in key:  # city and venue pages mix every kind: no category from them
                    entry[1].append(category)
        return {u: cats for k, (u, cats) in found.items() if "/category" not in k}

    async def _pages(self, urls: list[str]) -> list[str]:
        """Each page's HTML in the order given; a page that fails reads as empty."""
        pages = dict(zip(urls, await gather_or_cancel(self.optional(u, self.get_text(u)) for u in urls), strict=True))
        return [pages[u] or "" for u in urls]

    async def _listing_events(self, url: str) -> list[ScrapedEvent]:
        s, events, start = self.settings, [], url
        for _ in range(s.max_pages):
            page = await self.get_text(url)
            events += parse_rows(page, s.site_url + s.event_path, self.timezone, listing=start)
            next_link = _NEXT.search(page)
            if not next_link:
                return events
            url = urljoin(url, htmllib.unescape(next_link.group(1)))
        self.log.warning("%s truncated at max_pages=%d", url, s.max_pages)
        return events


def listing_links(page_html: str, base: str) -> dict[str, str]:
    """Listing page URLs by casefolded URL (the site links some in two casings)."""
    links = {}
    for href in _HREF.findall(page_html):
        url = urljoin(base, htmllib.unescape(href).replace(" ", "%20"))
        if url.startswith(base) and _LISTING.search(url):
            links.setdefault(url.casefold(), url)
    return links


def _text(fragment: str) -> str | None:
    return clean_str(re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip(" |"))


def category_name(url: str) -> str:
    """A category page's category: Category1/2 name the kind ("CONCERTS", "SOCCER"), Category3 a soccer league."""
    if "/category3/" in url.lower():
        return "soccer"
    return unquote(url.rsplit("/", 1)[-1].removesuffix(".aspx")).casefold()


def parse_rows(page_html: str, event_url: str, timezone: str, listing: str = "") -> list[ScrapedEvent]:
    """One event per row of a listing page; rows without an id, name or date are skipped."""
    events = []
    for row in _ROW.findall(page_html):
        eid, name, year, day = _EID.search(row), _NAME.search(row), _YEAR.search(row), _DAY.search(row)
        title = _text(name.group(1)) if name else None
        month = HEBREW_MONTHS.get(day.group(2)) if day else None
        if not (eid and title and year and day and month):
            continue
        # "venue | city | country"
        place = [p for p in (_text(p) for p in _PLACE.findall(row)) if p]
        country = country_code(place[-1] if place else None)
        city = place[-2] if len(place) >= 2 else None
        time = _TIME.search(row[day.end() :])
        price = _PRICE.search(row)
        sign = price.group(1).strip() if price else ""
        events.append(
            ScrapedEvent(
                source=LiveTicketsScraper.name,
                source_event_id=eid.group(1),
                url=event_url.format(eid=eid.group(1)),
                title=title,
                start_at=datetime(
                    int(year.group(1)),
                    month,
                    int(day.group(1)),
                    int(time.group(1)) if time else 0,
                    int(time.group(2)) if time else 0,
                    tzinfo=zone(local_zone(country, timezone) if time else timezone),  # venue's local time
                ),
                venue_name=place[0] if len(place) >= 3 else None,
                city=city,
                country=country,
                currency=CURRENCY_SIGNS.get(sign, "EUR"),
                min_price=to_decimal(price.group(2).replace(",", "").strip()) if price else None,
                extra=({"listing": listing} if listing else {})
                | ({"date_not_final": True} if "מועד לא סופי" in row else {}),
            )
        )
    return events
