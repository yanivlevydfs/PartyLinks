"""Xplorer (xplorer.co.il) - tickets to concerts, festivals and sports matches abroad (a flights site).

ASP.NET pages, no API. Two boards: shows (``/music``) and sport (``/sport``). Each carries its search
filters as JSON in a hidden input (``filterJson``), every value with its ``valid_events`` (event ids):
the numeric values are the artists' (teams', competitions') ids, ``type_*`` the sport, the rest
cities, months and ticket counts. Every board event is read (the count is the board's own). Each
artist page (``/music/artist/<id>``) lists that artist's shows by event id (an artist with one show
lists it only under "cheapest", whose id is in the JSON-LD offer link ``?ev=<id>``) and names the
artist. Details: the page with ``?ev=<id>`` shows that event's panel: local date and time, "city,
country" (Hebrew), venue, and its seat categories (USD price, tickets left); a match's page names it
in ``lblEventTitle``. One ``ScrapedEvent`` per event id; an event listed under several artists (a
festival) names them all. Team pages are not read (a match has no performers). ~2 + ~50 + ~650
requests (pages ~350 KB). robots.txt is absent (redirects to the 404 page).
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
_MATCH = re.compile(r"כרטיסים ל(.+?),?\s+בתאריך")
_PRICE_SUFFIX = re.compile(r"\s+מ-\s*\$[\d,.]+$")
# The sport board's type filters (type_<key>), as declared words (enrichment.declared_genres).
SPORTS = {"soccer": "soccer", "basketball": "basketball", "tennis": "tennis", "races": "motorsport"}


class XplorerSettings(SourceSettings):
    site_url: str
    music_path: str  # the shows board (artist ids in its filters)
    artist_path: str  # an artist's page: <artist_path><id>, one event: ?ev=<event id>
    sport_path: str  # the sport board (team / competition ids and the sport types in its filters)
    team_path: str  # a team's page: <team_path><id>, one match: ?ev=<event id>


class XplorerScraper(BaseScraper[XplorerSettings]):
    name = "xplorer"
    settings_model = XplorerSettings
    abroad_only = True

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        music = board_filters(await self.get_text(s.site_url + s.music_path))
        sport = board_filters(await self.get_text(s.site_url + s.sport_path))
        ids = [v for v in music if v.isdigit()]
        self.log.debug("listed %d artists", len(ids))

        async def artist(artist_id: str) -> tuple[str, str | None, list[str]]:
            page = await self.get_text(s.site_url + s.artist_path + artist_id)
            return artist_id, performer(page), event_ids(page)

        pages = {a[0]: a async for a in self.stream_isolated(ids, artist, label=lambda a: f"artist {a}")}
        # event id -> (page path, the artist / team page it is read from, every artist listing it)
        events: dict[str, tuple[str, str, list[str]]] = {}
        for artist_id in ids:
            _, name, listed = pages.get(artist_id, (artist_id, None, []))
            for event_id in dict.fromkeys([*listed, *music[artist_id]]):
                names = events.setdefault(event_id, (s.artist_path, artist_id, []))[2]
                if name and name not in names:
                    names.append(name)
        kinds = {e: SPORTS.get(k[5:], "sport") for k, listed in sport.items() if k.startswith("type_") for e in listed}
        for team_id in (v for v in sport if v.isdigit()):
            for event_id in sport[team_id]:
                events.setdefault(event_id, (s.team_path, team_id, []))
        # event id -> its board's English city ("San Francisco_U.S"): the local zone of a multi-zone country
        cities = {
            e: k.partition("_")[0]
            for board in (music, sport)
            for k, listed in board.items()
            if is_city(k)
            for e in listed
        }
        self.log.debug("listed %d events", len(events))

        async def detail(event_id: str) -> ScrapedEvent | None:
            path, page_id, names = events[event_id]
            url = f"{s.site_url}{path}{page_id}?ev={event_id}"
            kind = kinds.get(event_id, "sport") if path == s.team_path else None
            page = await self.get_text(url)
            return parse_event(page, url, event_id, self.timezone, names, sport=kind, city_en=cities.get(event_id))

        async for event in self.stream_isolated(list(events), detail, label=lambda e: f"event {e}"):
            yield event


def board_filters(page_html: str) -> dict[str, list[str]]:
    """A board's filters: each value (artist / team id, ``type_*``, city, month, count) -> its event ids."""
    node = LexborHTMLParser(page_html).css_first('input[name$="filterJson"]')
    try:
        filters = json.loads(node.attributes.get("value") or "") if node else None
    except json.JSONDecodeError:
        filters = None
    if not isinstance(filters, list):
        raise ParseError("board without its filters (blocked or layout changed?)")
    found: dict[str, list[str]] = {}
    for f in filters:
        if isinstance(f, dict) and f.get("value") is not None:
            listed = found.setdefault(str(f["value"]), [])
            listed.extend(e for e in dict.fromkeys(str(e) for e in f.get("valid_events") or []) if e not in listed)
    return found


def is_city(value: str) -> bool:
    """A board filter value naming a city ("City_Country"), not an id, a sport type or a month ("11_2026")."""
    return "_" in value and not value.startswith("type_") and not value.replace("_", "").isdigit()


def artist_ids(page_html: str) -> list[str]:
    """Artist ids of the shows board's filters (the other filters' values are cities, months, counts)."""
    return [v for v in board_filters(page_html) if v.isdigit()]


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
    page_html: str,
    url: str,
    event_id: str,
    timezone: str,
    artists: list[str] | None = None,
    sport: str | None = None,
    city_en: str | None = None,
) -> ScrapedEvent | None:
    """The event shown on an ``?ev=`` page; None when the page shows no event (pulled).

    ``sport``: a match of the sport board (its declared sport word): titled by its page, no performers.
    ``city_en``: the board's English city name, for the local zone (US cities' Hebrew names are not all known).
    """
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
        # a date alone: Israel's day
        tzinfo=zone(local_zone(country, timezone, city_en or clean_str(city)) if time else timezone),
    )
    # The page's JSON-LD events: this one's when listed there (its own artwork), else the page's first.
    nodes = _events(page_html)
    node = next((n for n in nodes if f"ev={event_id}" in str(n.get("offers"))), nodes[0] if nodes else {})
    if sport is not None:
        heading = tree.css_first("#TopFullPlaceHolder_lblEventTitle")
        match = _MATCH.search(" ".join(heading.text().split())) if heading else None
        title = clean_str(match.group(1)) if match else clean_str(_PRICE_SUFFIX.sub("", str(node.get("name") or "")))
        head, comma, tail = (title or "").rpartition(", ")
        if comma and tail in head:  # "A - B, A": the home team repeated (else the competition, kept)
            title = head
        if not title:
            raise ParseError(f"{url}: match without a title")
        performers: list[str] = []
        genres = [sport]
    else:
        artists = artists or [a for a in [performer(page_html)] if a]
        if not artists:
            raise ParseError(f"{url}: event without an artist")
        # A festival is listed as an "artist" of its own ("פסטיבל פרימוורה"): it names the event, not a performer.
        festival = next((a for a in artists if a.startswith("פסטיבל")), None)
        performers = [a for a in artists if a != festival]
        title = festival or " / ".join(performers)
        genres = ["festival" if festival else "concert"]  # the shows board sells music only

    def cell(field: str, i: int) -> str | None:
        node = tree.css_first(f"#TopFullPlaceHolder_ticketsList_{field}_{i}")
        return clean_str(" ".join(node.text().split())) if node else None

    tickets = []
    for i, ticket in enumerate(tree.css("input[id^=TopFullPlaceHolder_ticketsList_ticketId_]")):
        price = re.search(r"\$\s*([\d,.]+)", cell("lblTicketPrice", i) or "")
        left = re.search(r"\d+", cell("lblTicketsLeft", i) or "")
        tickets.append(
            TicketTier(
                source_ticket_id=ticket.attributes.get("value"),
                name=cell("lblTicketType", i) or "כרטיס",
                description=cell("lblTicketDesc", i),
                price=to_decimal(price.group(1).replace(",", "")) if price else None,
                currency="USD",
                remaining=to_int(left.group(0)) if left else None,
            )
        )
    prices = [t.price for t in tickets if t.price is not None]
    images = node.get("image") if isinstance(node.get("image"), list) else [node.get("image")]
    return ScrapedEvent(
        source=XplorerScraper.name,
        source_event_id=event_id,
        url=url,
        title=title,
        source_genres=genres,
        start_at=start_at,
        time_known=time is not None,
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
