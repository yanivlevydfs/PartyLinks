"""EventPro (Interdeal) community-centre box offices - shows, children's plays, stand-up, lectures, films
and day trips sold by a regional council's or a city's community centre (mk-hefer.org.il,
matnasdn.co.il).

Every site is the same IIS app in windows-1255. A listing (one category page ``/html5/?_id=<id>&G=<id>``,
or the home page) shows 16 cards a page, the next ones at ``/html5/sbs.taf?_id=<id>&g=<id>&did=<did>
&_startPRO4=<17, 33, ...>``; each card has its title, date, hour, place, prices and picture, all in
``<span class="productBlock Product...">`` blocks with a ``.value``. The event page
(``EventproLookup.taf?_function=details&_ID=<id>``) repeats them with the place in full, the
description and the tickets left. Courses, clubs and job ads share the listing: dropped by title words.
robots.txt allows everything but /Common/. 1 + ~8 listing pages and one page per event a site.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import datetime, time
from decimal import Decimal
from urllib.parse import urljoin

from pydantic import BaseModel, ConfigDict, Field
from selectolax.lexbor import LexborHTMLParser, LexborNode

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, html_to_text, to_decimal, to_int, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_ID = re.compile(r"_ID=(\d+)", re.IGNORECASE)
_DATE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{2,4})")
_CLOCK = re.compile(r"(\d{1,2}):(\d{2})")
# "בווייז: בית הראשונים, ביתן אהרון - מקום מפגש: ..." / "מול הפוליצר - חניה... ווייז לכתוב: בית העם כפר ויתקין"
_WAZE = re.compile(r"וו?ייז(?: לכתוב)?:\s*([^-(]+)")


class EventProSite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str  # https://www.mk-hefer.org.il
    did: int  # the site's id in every link (4547)
    listing_id: int | None = None  # the "all events" category; None = the home page lists them
    name: str  # the store's name: מרכז קהילתי עמק חפר
    city: str
    # Regional council: a place naming one of these localities is in that locality, not in `city`.
    localities: list[str] = Field(default_factory=list)
    # Category pages whose events get the category's name as a genre: {13391: ילדים}.
    categories: dict[int, str] = Field(default_factory=dict)
    # The hall is the venue and the place its street (libraries). Elsewhere the hall is a room
    # ("אודיטוריום") and the place names the building.
    hall_is_venue: bool = False


class EventProSettings(SourceSettings):
    sites: list[EventProSite] = Field(min_length=1)
    page_size: int = Field(16, ge=1)
    max_pages: int = Field(20, ge=1)
    # Courses, clubs, season tickets, job ads: listed with the shows, not events.
    skip_title_words: list[str] = Field(default_factory=list)


class EventProScraper(BaseScraper[EventProSettings]):
    name = "eventpro"
    settings_model = EventProSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        for site in self.settings.sites:
            async for event in self._site(site):
                yield event

    async def _site(self, site: EventProSite) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        cards: dict[str, ScrapedEvent] = {}
        async for page_html in self._pages(site, site.listing_id):
            found = parse_listing(page_html, site, self.timezone, s.skip_title_words)
            fresh = {e.source_event_id: e for e in found if e.source_event_id not in cards}
            cards.update(fresh)
            if not fresh:
                break
        for category_id, genre in site.categories.items():
            seen: set[str] = set()
            async for page_html in self._pages(site, category_id):
                ids = {f"{site.did}:{i}" for i in card_ids(page_html)} - seen
                if not ids:
                    break
                seen |= ids
                for key in ids & cards.keys():
                    cards[key].source_genres.append(genre)
        events = [e for e in cards.values() if is_current(e.start_at, e.end_at, RetentionSettings(), self.timezone)]
        self.log.debug("%s: listed %d upcoming events", site.url, len(events))

        async def read(event: ScrapedEvent) -> ScrapedEvent:
            return add_details(event, await self.get_text(str(event.url)), site)

        async for event in self.stream_isolated(events, read, lambda e: f"{site.url} event {e.source_event_id}"):
            yield event

    async def _pages(self, site: EventProSite, listing_id: int | None) -> AsyncIterator[str]:
        """The pages of one listing (the home page alone without an id); the caller stops at no new card."""
        for page in range(self.settings.max_pages if listing_id else 1):
            yield await self.get_text(listing_url(site, page * self.settings.page_size + 1, listing_id))


def listing_url(site: EventProSite, start: int, listing_id: int | None = None) -> str:
    i = listing_id or site.listing_id
    if not i:
        return site.url + "/"
    return f"{site.url}/html5/sbs.taf?_id={i}&sm=&g={i}&did={site.did}&_startPRO4={start}"


def card_ids(page_html: str) -> list[str]:
    """Event ids of a listing page's cards."""
    return list(
        dict.fromkeys(
            m
            for a in LexborHTMLParser(page_html).css("article a[href]")
            if (m := (_ID.search(a.attributes.get("href") or "") or [None, None])[1])
        )
    )


def _value(node: LexborNode | LexborHTMLParser, block: str) -> str | None:
    found = node.css_first(f".productBlock.{block} .value")
    return clean_str(found.text()) if found else None


def clean_place(place: str | None) -> str | None:
    """The place without parking and Waze directions."""
    if not place:
        return None
    waze = _WAZE.search(place)
    if waze:
        place = waze.group(1)
    place = re.sub(r"\([^)]*\)", "", place).split(" - ")[0]
    return clean_str(place.replace("ׁ", ""))  # "ויתׁקין": a stray shin dot


def _place_city(place: str | None, site: EventProSite) -> str:
    return next((loc for loc in site.localities if place and loc in place), site.city)


def parse_listing(page_html: str, site: EventProSite, timezone: str, skip_words: list[str]) -> list[ScrapedEvent]:
    """The cards of one listing page that are dated events (not courses or ads)."""
    events = []
    for card in LexborHTMLParser(page_html).css("article"):
        link = card.css_first("a.productName") or card.css_first("a[href*='_function=details']")
        href = link.attributes.get("href") if link else None
        found = _ID.search(href or "")
        heading = card.css_first("h2")
        title = clean_str(heading.text()) if heading else None
        day = _DATE.search(_value(card, "ProductSaleDate") or "")
        if not found or not title or not day or any(w in title for w in skip_words):
            continue
        d, m, y = (int(x) for x in day.groups())
        clock = _CLOCK.search(_value(card, "ProductSaleTime") or "")
        at = time(int(clock.group(1)) % 24, int(clock.group(2))) if clock else time()
        start = datetime(y + 2000 if y < 100 else y, m, d, at.hour, at.minute, tzinfo=zone(timezone))
        prices: list[Decimal] = sorted(
            p for i in card.css("input[type=hidden]") if (p := to_decimal(i.attributes.get("value"))) is not None
        )
        img = card.css_first("img.picarc")
        src = img.attributes.get("src") if img else None
        place = clean_place(_value(card, "ProductPlace"))
        url = f"{site.url}/html5/EventproLookup.taf?_function=details&_ID={found.group(1)}&did={site.did}"
        events.append(
            ScrapedEvent(
                source=EventProScraper.name,
                source_event_id=f"{site.did}:{found.group(1)}",
                url=url,
                title=title,
                start_at=start,
                time_known=clock is not None,
                venue_name=place,
                city=_place_city(place or title, site),
                organizer=site.name,
                images=[urljoin(site.url + "/", src)] if src else [],
                min_price=prices[0] if prices else None,
                max_price=prices[-1] if prices else None,
                extra={"purchase_url": url},
            )
        )
    return events


def add_details(event: ScrapedEvent, page_html: str, site: EventProSite) -> ScrapedEvent:
    """The event page's place in full, description, sold-out state and big picture."""
    tree = LexborHTMLParser(page_html)
    place = clean_place(_value(tree, "ProductLocation"))
    if place:
        event.venue_name = place
        event.city = _place_city(place, site)
    # "אולם: מדיה-טק העיר", "מיקום: שד' בנימין 60 נתניה" (sifrianet): the hall names the venue, the
    # place is its street.
    hall = clean_place(_value(tree, "ProductHall")) if site.hall_is_venue else None
    if hall and place and hall != place:
        event.venue_name, event.address = hall, place
    desc = tree.css_first(".productBlock.productShortDes .value")
    if desc:
        # Without the site's own links ("פעילות ילדים בעמק חפר >>"): not this event's words.
        text = html_to_text(desc.html) or ""
        event.description = "\n".join(line for line in text.splitlines() if ">>" not in line) or None
    left = to_int(_value(tree, "ProductTicketAmount"))
    event.is_sold_out = left == 0
    big = tree.css_first("img.bigPic")
    if big and big.attributes.get("src"):
        event.images = list(dict.fromkeys([urljoin(site.url + "/", big.attributes["src"] or ""), *event.images]))
    return event
