"""Xplorer (xplorer.co.il) - tickets to concerts and festivals abroad (a flights site's shows board).

ASP.NET pages, no API. The shows board (``/music``) carries its search filters as JSON in a hidden
input (``filterJson``): the numeric values are the artists' ids. Each artist page
(``/music/artist/<id>``) lists that artist's shows by event id (an artist with one show lists it
only under "cheapest", whose id is in the JSON-LD offer link ``?ev=<id>``). Details: the page with
``?ev=<id>`` shows that event's panel: local date and time, "city, country" (Hebrew), venue, and
its seat categories (USD price, tickets left). One ``ScrapedEvent`` per event id; an event listed
under several artists (a festival) names them all. ~1 + ~50 + ~340 requests (pages ~350 KB).
robots.txt is absent (redirects to the 404 page).
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from datetime import datetime

from selectolax.lexbor import LexborHTMLParser

from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import ParseError, clean_str, jsonld_nodes, schema_types, to_decimal, to_int, zone
from partylinks.scrapers.abroad import country_code, local_zone
from partylinks.scrapers.base import BaseScraper, SourceSettings

PANEL = "#TopFullPlaceHolder_lblEventLocation"
_EVENT_ID = re.compile(r'event_id="(\d+)"')
_EV_PARAM = re.compile(r"[?&]ev=(\d+)")
_DATE = re.compile(r"(\d{2})/(\d{2})/(\d{2})")
_TIME = re.compile(r"(\d{1,2}):(\d{2})")
_PLACE = re.compile(r"עיר:\s*(.+?)\s*(?:מיקום:\s*(.*))?$")
_TITLE = re.compile(r"<title>\s*כרטיסים ל(.+?)\s*\|")


class XplorerSettings(SourceSettings):
    site_url: str
    music_path: str  # the shows board (artist ids in its filters)
    artist_path: str  # an artist's page: <artist_path><id>, one event: ?ev=<event id>


class XplorerScraper(BaseScraper[XplorerSettings]):
    name = "xplorer"
    settings_model = XplorerSettings
    abroad_only = True

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        ids = artist_ids(await self.get_text(s.site_url + s.music_path))
        self.log.info("listed %d artists", len(ids))

        async def artist(artist_id: str) -> tuple[str, str | None, list[str]]:
            page = await self.get_text(s.site_url + s.artist_path + artist_id)
            return artist_id, performer(page), event_ids(page)

        pages = {a[0]: a async for a in self.stream_isolated(ids, artist, label=lambda a: f"artist {a}")}
        # event id -> (artist page it is read from, every artist listing it, in the board's order)
        events: dict[str, tuple[str, list[str]]] = {}
        for artist_id, name, listed in (pages[i] for i in ids if i in pages):
            for event_id in listed:
                names = events.setdefault(event_id, (artist_id, []))[1]
                if name and name not in names:
                    names.append(name)
        self.log.info("listed %d events", len(events))

        async def detail(event_id: str) -> ScrapedEvent | None:
            artist_id, names = events[event_id]
            url = f"{s.site_url}{s.artist_path}{artist_id}?ev={event_id}"
            return parse_event(await self.get_text(url), url, event_id, self.timezone, names)

        async for event in self.stream_isolated(list(events), detail, label=lambda e: f"event {e}"):
            yield event


def artist_ids(page_html: str) -> list[str]:
    """Artist ids of the shows board's filters (the other filters' values are cities, months, counts)."""
    node = LexborHTMLParser(page_html).css_first('input[name$="filterJson"]')
    try:
        filters = json.loads(node.attributes.get("value") or "") if node else None
    except json.JSONDecodeError:
        filters = None
    if not isinstance(filters, list):
        raise ParseError("shows board without its filters (blocked or layout changed?)")
    return list(
        dict.fromkeys(str(f["value"]) for f in filters if isinstance(f, dict) and str(f.get("value")).isdigit())
    )


def event_ids(page_html: str) -> list[str]:
    """An artist page's event ids: its show rows (0 = the "cheapest" row) and JSON-LD offer links."""
    found = [*_EVENT_ID.findall(page_html), *_EV_PARAM.findall(page_html)]
    return [i for i in dict.fromkeys(found) if i != "0"]


def _events(page_html: str) -> list[dict]:
    return [n for n in jsonld_nodes(page_html) if "event" in schema_types(n)]


def performer(page_html: str) -> str | None:
    """The artist's name (Hebrew) of an artist page: its JSON-LD performer, else the page title."""
    for node in _events(page_html):
        if isinstance(node.get("performer"), dict) and (name := clean_str(node["performer"].get("name"))):
            return name
    title = _TITLE.search(page_html)
    return clean_str(title.group(1)) if title else None


def parse_event(
    page_html: str, url: str, event_id: str, timezone: str, artists: list[str] | None = None
) -> ScrapedEvent | None:
    """The event shown on an ``?ev=`` page; None when the page shows no event (pulled)."""
    tree = LexborHTMLParser(page_html)
    panel = tree.css_first(PANEL)
    text = " ".join(panel.text().split()) if panel else ""
    date = _DATE.search(text)
    if not date:
        return None
    time = _TIME.search(text[date.end() :])
    place = _PLACE.search(text)
    city, _, country_name = (place.group(1) if place else "").rpartition(",")
    country = country_code(country_name)
    start_at = datetime(
        2000 + int(date.group(3)),
        int(date.group(2)),
        int(date.group(1)),
        int(time.group(1)) if time else 0,
        int(time.group(2)) if time else 0,
        tzinfo=zone(local_zone(country, timezone) if time else timezone),  # a date alone: Israel's day
    )
    artists = artists or [a for a in [performer(page_html)] if a]
    if not artists:
        raise ParseError(f"{url}: event without an artist")
    # A festival is listed as an "artist" of its own ("פסטיבל פרימוורה"): it names the event, not a performer.
    festival = next((a for a in artists if a.startswith("פסטיבל")), None)
    performers = [a for a in artists if a != festival]

    def cell(field: str, i: int) -> str | None:
        node = tree.css_first(f"#TopFullPlaceHolder_ticketsList_{field}_{i}")
        return clean_str(" ".join(node.text().split())) if node else None

    tickets = []
    for i, node in enumerate(tree.css("input[id^=TopFullPlaceHolder_ticketsList_ticketId_]")):
        price = re.search(r"\$\s*([\d,.]+)", cell("lblTicketPrice", i) or "")
        left = re.search(r"\d+", cell("lblTicketsLeft", i) or "")
        tickets.append(
            TicketTier(
                source_ticket_id=node.attributes.get("value"),
                name=cell("lblTicketType", i) or "כרטיס",
                description=cell("lblTicketDesc", i),
                price=to_decimal(price.group(1).replace(",", "")) if price else None,
                currency="USD",
                remaining=to_int(left.group(0)) if left else None,
            )
        )
    prices = [t.price for t in tickets if t.price is not None]
    # The artist's JSON-LD events: this one's when listed there (its own artwork), else the artist's.
    nodes = _events(page_html)
    node = next((n for n in nodes if f"ev={event_id}" in str(n.get("offers"))), nodes[0] if nodes else {})
    images = node.get("image") if isinstance(node.get("image"), list) else [node.get("image")]
    return ScrapedEvent(
        source=XplorerScraper.name,
        source_event_id=event_id,
        url=url,
        title=festival or " / ".join(performers),
        source_genres=["festival" if festival else "concert"],  # the board sells music only
        start_at=start_at,
        venue_name=clean_str(place.group(2)) if place and place.group(2) else None,
        city=clean_str(city),
        country=country,
        artists=performers,
        images=[i for i in images if isinstance(i, str) and i],
        currency="USD",
        min_price=min(prices, default=None),
        max_price=max(prices, default=None),
        is_sold_out=not tickets,
        tickets=tickets,
    )
