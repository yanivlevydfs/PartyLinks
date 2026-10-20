"""Eventbuzz (eventbuzz.co.il) - independent productions and community events.

The site has no JSON API (server-rendered PHP pages), so this module parses HTML
with CSS selectors that all live in the config. Listing: one page (``/events``)
of cards linking to ``/lp/event/<slug>``; no pagination. Details: each event
page (title, start, location line, description, artwork, ticket table with the
price / remaining count / on-sale state of every tier).
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import datetime
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from selectolax.lexbor import LexborHTMLParser, LexborNode

from partylinks.models import ScrapedEvent, TicketTier, TierStatus
from partylinks.parsing import ParseError, clean_str, html_to_text, to_decimal, to_int
from partylinks.scrapers.base import BaseScraper, SourceSettings

_DATETIME = re.compile(r"(\d{1,2}):(\d{2})\s+(\d{1,2})\.(\d{1,2})\.(\d{4})")
_EVENT_ID = re.compile(r"var event_id = '(\w+)'")


class EventbuzzSelectors(BaseModel):
    card_link: str
    info_items: str
    description: str
    ticket_row: str
    ticket_name: str
    ticket_price: str
    ticket_original_price: str
    ticket_remaining: str
    ticket_on_sale: str
    service_fee: str


class EventbuzzLabels(BaseModel):
    """Prefixes of the event-info lines (Hebrew UI labels)."""

    start: str
    location: str


class EventbuzzSettings(SourceSettings):
    site_url: str
    listing_path: str
    event_path_prefix: str
    selectors: EventbuzzSelectors
    labels: EventbuzzLabels


class EventbuzzScraper(BaseScraper[EventbuzzSettings]):
    name = "eventbuzz"
    settings_model = EventbuzzSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        urls = parse_listing(await self.get_text(s.site_url + s.listing_path), s)
        self.log.info("listed %d events", len(urls))
        async for event in self.stream_isolated(urls, self._fetch_event, label=lambda u: u):
            yield event

    async def _fetch_event(self, url: str) -> ScrapedEvent:
        return parse_event(await self.get_text(url), url, self.settings, self.timezone)


def parse_listing(page_html: str, s: EventbuzzSettings) -> list[str]:
    hrefs = (n.attributes.get("href") or "" for n in LexborHTMLParser(page_html).css(s.selectors.card_link))
    return list(dict.fromkeys(urljoin(s.site_url, h) for h in hrefs if s.event_path_prefix in h))


def parse_event(page_html: str, url: str, s: EventbuzzSettings, timezone: str) -> ScrapedEvent:
    tree = LexborHTMLParser(page_html)
    sel, labels = s.selectors, s.labels
    info = [text for node in tree.css(sel.info_items) if (text := clean_str(_text(node)))]
    title = info[0] if info and not info[0].startswith((labels.start, labels.location)) else _meta(tree, "og:title")
    if not title:
        raise ParseError("event title not found")

    start_at = None
    if (start := _labelled(info, labels.start)) and (m := _DATETIME.search(start)):
        hour, minute, day, month, year = map(int, m.groups())
        start_at = datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(timezone))

    # "venue, street, city" - fewer parts means the city (or street) was left out.
    location = _labelled(info, labels.location)
    parts = [p.strip() for p in (location or "").split(",") if p.strip()]
    venue = parts[0] if parts else None
    address = ", ".join(parts[1:]) or None
    city = parts[-1] if len(parts) >= 3 else None

    tickets = _tickets(tree, sel)
    prices = [t.price for t in tickets if t.price is not None]
    event_id = _EVENT_ID.search(page_html)
    description = tree.css_first(sel.description)
    fee = tree.css_first(sel.service_fee)
    return ScrapedEvent(
        source=EventbuzzScraper.name,
        source_event_id=event_id.group(1) if event_id else url.rstrip("/").rsplit("/", 1)[-1],
        url=url,
        title=title,
        description=(html_to_text(description.html) if description else None) or _meta(tree, "og:description"),
        start_at=start_at,
        venue_name=venue,
        address=address,
        city=city,
        image_url=_meta(tree, "og:image"),
        min_price=min(prices, default=None),
        max_price=max(prices, default=None),
        is_sold_out=bool(tickets) and all(not t.is_active for t in tickets),
        tickets=tickets,
        extra={"location": location, "service_fee_sum": fee.attributes.get("value") if fee else None},
    )


def _tickets(tree: LexborHTMLParser, sel: EventbuzzSelectors) -> list[TicketTier]:
    """The table is rendered twice (desktop + mobile): keep the first row per tier id."""
    rows: dict[str, LexborNode] = {}
    for row in tree.css(sel.ticket_row):
        rows.setdefault(row.attributes.get("id") or "", row)
    tickets = []
    for row_id, row in rows.items():
        name = row.css_first(sel.ticket_name)
        price = to_decimal(_value(row, sel.ticket_price))
        tickets.append(
            TicketTier(
                source_ticket_id=row_id.removeprefix("tid-") or None,
                name=(clean_str(_text(name)) if name else None) or "כרטיס",
                price=price,
                original_price=to_decimal(_value(row, sel.ticket_original_price)),
                is_free=price == 0,
                remaining=to_int(_value(row, sel.ticket_remaining)),
                # No quantity picker = this round's sale ended (or never opened).
                status=TierStatus.ON_SALE if row.css_first(sel.ticket_on_sale) else TierStatus.CLOSED,
            )
        )
    return tickets


def _value(node: LexborNode, selector: str) -> str | None:
    found = node.css_first(selector)
    return found.attributes.get("value") if found else None


def _meta(tree: LexborHTMLParser, prop: str) -> str | None:
    node = tree.css_first(f'meta[property="{prop}"]')
    return clean_str(node.attributes.get("content")) if node else None


def _text(node: LexborNode) -> str:
    return " ".join(node.text(separator=" ").split())


def _labelled(lines: list[str], label: str) -> str | None:
    for line in lines:
        if line.startswith(label):
            return clean_str(line[len(label) :].lstrip(": "))
    return None
