"""Municipal event calendars on one shared CMS (rosh-haayin.muni.il, kfar-saba.muni.il, ...).

Several municipalities run the same site engine: ``/events/`` lists every upcoming event as
``a.event-promo`` cards (no pagination) with title,
date ("יום ראשון, 01.11.2026", or "מיום ... עד יום ..." for a season), time ("19:30" or
"17:30 - 20:30"), place, thumbnail, tags (#נשים #סדנה) and a ribbon ("אזל המלאי", "נותרו רק 5
כרטיסים"). Each card's page ``/events/<id>/`` adds the description, the full-size image and, when
the city sells tickets itself, a tickets row ("40 ₪ • אירוע בתשלום"). Most are free community
events sold nowhere else; cards that leave the site (a Smarticket hall, a news item) are skipped,
their seller has its own source. Undated cards ("ללא תאריך", "עד ...") are skipped. No robots.txt
(404). A bare user agent is redirected to the host's abuse page (abuse.spd.co.il). A recurring
event repeats its card once per date (Kfar Saba: ~570 cards, 83 events); it is kept once, at its
next date (cards run in date order), so its id rolls forward instead of flooding the calendar.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from urllib.parse import urljoin, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field
from selectolax.lexbor import LexborHTMLParser, LexborNode

from partylinks.models import ScrapedEvent, TicketTier, TierStatus
from partylinks.parsing import clean_str, html_to_text, zone
from partylinks.scrapers.base import BaseScraper, SourceSettings

_DATE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")
_CLOCK = re.compile(r"(\d{1,2}):(\d{2})")
_PRICE = re.compile(r"(\d+(?:\.\d+)?)\s*₪")
_LEFT = re.compile(r"נותרו רק (\d+)")
_EVENT_ID = re.compile(r"/events/(\d+)/?$")


class MuniSite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str  # https://www.rosh-haayin.muni.il  (the listing is <url>/events/)
    city: str


class MuniEventsSettings(SourceSettings):
    sites: list[MuniSite] = Field(min_length=1)


@dataclass
class Card:
    """One listing card, before its page is read."""

    url: str
    event_id: str
    title: str
    date_text: str
    time_text: str
    place: str | None
    image: str | None
    tags: list[str]
    ribbon: str | None


class MuniEventsScraper(BaseScraper[MuniEventsSettings]):
    name = "munievents"
    settings_model = MuniEventsSettings

    def is_blocked(self, response: httpx.Response) -> bool:
        return response.url.host.startswith("abuse.")

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        for site in self.settings.sites:
            page = await self.optional(f"{site.url} listing", self.get_text(site.url + "/events/"))
            cards = parse_listing(page, site.url) if page else []
            self.log.info("%s: listed %d events", site.city, len(cards))

            async def fetch(card: Card, site: MuniSite = site) -> ScrapedEvent | None:
                detail = await self.optional(card.url, self.get_text(card.url))
                return build_event(card, detail, site, self.timezone)

            async for event in self.stream_isolated(cards, fetch, label=lambda c: c.url):
                yield event


def _text(node: LexborNode | None) -> str:
    return " ".join((node.text() if node else "").split())


def parse_listing(page_html: str, base: str) -> list[Card]:
    """The site's own event cards; links off the site are left out."""
    cards: dict[str, Card] = {}
    for a in LexborHTMLParser(page_html).css("a.event-promo"):
        url = urljoin(base + "/", a.attributes.get("href") or "")
        found = _EVENT_ID.search(urlsplit(url).path)
        title = _text(a.css_first(".name"))
        if urlsplit(url).netloc != urlsplit(base).netloc or not found or not title:
            continue
        img = a.css_first(".pic img")
        cards.setdefault(
            url,
            Card(
                url=url,
                event_id=found.group(1),
                title=title,
                date_text=_text(a.css_first(".date")),
                time_text=_text(a.css_first(".time")),
                place=clean_str(_text(a.css_first(".place"))),
                image=urljoin(base + "/", img.attributes["src"] or "") if img and img.attributes.get("src") else None,
                tags=[t.lstrip("#") for t in (_text(b) for b in a.css(".tags .badge")) if t],
                ribbon=clean_str(_text(a.css_first(".ribbon"))),
            ),
        )
    return list(cards.values())


def _split_place(place: str | None) -> tuple[str | None, str | None]:
    """Venue and address of "מרכז פיס תורני - רח' המתמיד 23"; several places stay whole."""
    if not place or "|" in place:
        return place, None
    venue, *address = re.split(r"\s+-\s+|,\s+", place, maxsplit=1)
    return clean_str(venue), clean_str(address[0]) if address else None


def build_event(card: Card, detail_html: str | None, site: MuniSite, timezone: str) -> ScrapedEvent | None:
    """The card plus what its page adds; None when the card has no start date."""
    day = _DATE.search(card.date_text)
    if not day or card.date_text.startswith("עד"):
        return None
    on = date(int(day.group(3)), int(day.group(2)), int(day.group(1)))
    clocks = [time(int(h) % 24, int(m)) for h, m in _CLOCK.findall(card.time_text)]
    tz = zone(timezone)
    start = datetime.combine(on, clocks[0] if clocks else time(), tz)
    end = datetime.combine(on, clocks[1], tz) if len(clocks) > 1 else None

    description = image = price_text = None
    sold_out = card.ribbon == "אזל המלאי"
    if detail_html:
        page = LexborHTMLParser(detail_html)
        content = page.css_first("#event-page-content .rich-content")
        description = html_to_text(content.html) if content else None
        pic = page.css_first(".event-item-pic img")
        image = urljoin(site.url + "/", pic.attributes["src"] or "") if pic and pic.attributes.get("src") else None
        tickets = page.css_first(".event-item-row.tickets")
        if tickets:
            price_text = _text(tickets.css_first(".event-item-value"))
            sold_out = sold_out or "הכרטיסים אזלו" in _text(tickets)
    prices = [Decimal(p) for p in _PRICE.findall(price_text or "")]
    left = _LEFT.search(card.ribbon or "")
    status = TierStatus.SOLD_OUT if sold_out else TierStatus.ON_SALE
    tiers = [
        TicketTier(name="כרטיס", price=p, status=status, remaining=int(left.group(1)) if left else None) for p in prices
    ]
    if not prices and price_text:  # "אירוע בהרשמה מראש": registration, price not stated
        tiers = [TicketTier(name="הרשמה", status=status)]
    venue, address = _split_place(card.place)
    host = urlsplit(site.url).netloc.removeprefix("www.").split(".")[0]  # rosh-haayin
    return ScrapedEvent(
        source=MuniEventsScraper.name,
        source_event_id=f"{host}:{card.event_id}",
        url=card.url,
        title=card.title,
        description=description,
        start_at=start,
        end_at=end,
        venue_name=venue,
        address=address,
        city=site.city,
        source_genres=card.tags,
        organizer=f"עיריית {site.city}",
        image_url=image or card.image,  # the page's full size, else the card's thumbnail
        min_price=min(prices) if prices else None,
        max_price=max(prices) if prices else None,
        is_sold_out=sold_out,
        tickets=tiers,
    )
