"""Tzavta (tzavta.co.il, "צוותא"), Ibn Gabirol 30, Tel Aviv - theatre, stand-up, music, children's shows,
the opera and literary series and the Short Theatre Festival ("פסטיבל תיאטרון קצר", ``/category/17``).

The site is ASP.NET under Cloudflare (answers direct). Its sections, all read every run:

* the menu's categories ``/category/<n>`` (תיאטרון, מוזיקה, סטנד אפ, הורים וילדים, מיוחדים, the festival,
  the series): ids 1..``category_scan_max`` are tried (a missing one answers 404, skipped), plus any category the
  home page links. Each page lists its events (``a.shows_link`` cards to ``/event/<id>``);
* the schedule board ``/shows`` (every coming performance, the months' pages are its slices) and the home page,
  for events no category lists;
* every event page ``/event/<id>``: its ``const model = {...}`` script holds the title, subtitle, texts, artwork
  (``/download/<Image>``), the box-office code (``TicketsCode``, the Smarticket show id), ``ExternalSale`` and the
  performances (``ShowsList``, or ``MainShow`` for one date): date, time and hall (``Show_Place``). A page with no
  performance is a series' or the festival's umbrella page, a call for entries or a gift voucher: no event; the
  event pages it links are read too and inherit its categories (the festival's guest shows).

Tickets: the site's own iframe is Smarticket (``tzavta.smarticket.co.il``). One ``/api/shows`` call there maps
(show id, date, time) to the performance's page ``/event/<id>``, the purchase link the ``smarticket`` source
lists, so dedup merges the two; with its price list. A performance Smarticket does not list gets the
redirect's target (``/iframe/api/redirect_event?key=<code>/<date>/<hhmm>``). ``ExternalSale`` shows link
another box office in their text (the artist's own site): that link.

Places: the halls (צוותא 1, צוותא 2, the office "משרד", BABU bar) are rooms of the one building: venue
צוותא with the hall in ``extra["hall"]``; ``places`` maps the places elsewhere (a road show at Kibbutz Yagur).
The category "צוותא 2" names a hall, not a genre. robots.txt disallows only /bin/, /.well-known/ and previews.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, time, timedelta
from functools import cache
from html import unescape
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field
from selectolax.lexbor import LexborHTMLParser

from partylinks.config import RetentionSettings, get_settings
from partylinks.models import ScrapedEvent, TicketTier, TierStatus
from partylinks.parsing import ParseError, clean_str, html_to_text, to_decimal, zone
from partylinks.places import PlaceIndex, all_cities
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.smarticket import iter_performances, parse_shows_body

_MODEL = "const model = "
_EVENT_LINK = re.compile(r"""href=["']\s*(?:https?://(?:www\.)?tzavta\.co\.il)?/+event/(\d+)""", re.IGNORECASE)
_CATEGORY_LINK = re.compile(r"""href=["']\s*(?:https?://(?:www\.)?tzavta\.co\.il)?/+category/(\d+)""", re.IGNORECASE)
_HREF = re.compile(r"""href=["'](https?://[^"'\s]+)""")
_CLOCK = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_CANCELLED = re.compile(r"לא\s+תתקיים|בוטל|נדחה")
_SOLD_OUT = re.compile(r"אזל|sold\s*out", re.IGNORECASE)


class TzavtaPlace(BaseModel):
    """A place outside the building, as the site names it in ``Show_Place`` ("יגור")."""

    model_config = ConfigDict(extra="forbid")

    venue_name: str
    city: str
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class TzavtaSettings(SourceSettings):
    site_url: str = "https://www.tzavta.co.il"
    category_path: str = "/category/{id}"
    event_path: str = "/event/{id}"
    board_path: str = "/shows"
    image_path: str = "/download/"
    # Category ids tried each run (1..this); ids the home page links are read too.
    category_scan_max: int = Field(60, ge=1, le=500)
    # Categories that name a hall, not a genre ("צוותא 2"): read for their events, not declared.
    hall_categories: list[str] = Field(default_factory=list)
    # A word in a category's name -> the words declared beside it ("מיוחדים" -> "מופע").
    labels: dict[str, list[str]] = Field(default_factory=dict)
    ticket_host: str = "tzavta.smarticket.co.il"
    smarticket_shows_path: str = "/api/shows"
    smarticket_event_path: str = "/event/{id}"
    redirect_path: str = "/iframe/api/redirect_event?key={code}/{date}/{hhmm}"
    # Hosts of profiles and shares, never a ticket link.
    social_hosts: list[str] = Field(
        default_factory=lambda: ["facebook.com", "instagram.com", "youtube.com", "twitter.com", "linktr.ee",
                                 "tiktok.com", "wa.me", "whatsapp.com", "google.com", "inwise.net"]
    )  # fmt: skip
    # Show_Place values that are a status, not a hall ("Sold Out!"): the building, sold out.
    sold_out_places: list[str] = Field(default_factory=lambda: ["sold out"])
    # Show_Place values that are rooms of the building ("צוותא 1", "משרד", "BABU בר").
    halls: list[str] = Field(default_factory=list)
    venue_name: str
    address: str
    city: str
    latitude: float | None = None
    longitude: float | None = None
    places: dict[str, TzavtaPlace] = Field(default_factory=dict)


class TzavtaScraper(BaseScraper[TzavtaSettings]):
    name = "tzavta"
    settings_model = TzavtaSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        home = await self.optional("home page", self.get_text(s.site_url + "/")) or ""
        board = await self.get_text(s.site_url + s.board_path)  # the full schedule: must answer
        category_ids = sorted({*range(1, s.category_scan_max + 1), *map(int, _CATEGORY_LINK.findall(home))})
        categories: dict[str, list[str]] = {}  # event id -> its categories' names
        async for name, ids in self.stream_isolated(category_ids, self._category, label=lambda n: f"category {n}"):
            for event_id in ids:
                categories.setdefault(event_id, [])
                if name not in categories[event_id]:
                    categories[event_id].append(name)
        board_ids = event_ids(board)
        ordered = list(dict.fromkeys([*board_ids, *categories, *event_ids(home)]))  # the board's date order first
        offers = await self.optional("smarticket shows", self._offers()) or {}

        pages: dict[str, dict[str, Any]] = {}
        async for event_id, model in self.stream_isolated(ordered, self._model, label=lambda i: f"event {i}"):
            pages[event_id] = model
        # Umbrella pages (a festival, a series) list their shows by link: read the ones not yet read.
        linked: list[str] = []
        for event_id, model in pages.items():
            for other in event_ids(f"{model.get('Decription') or ''} {model.get('Editor') or ''}"):
                if other in pages or other in linked:
                    continue
                linked.append(other)
            if not performances(model):
                for other in event_ids(f"{model.get('Decription') or ''} {model.get('Editor') or ''}"):
                    names = categories.setdefault(other, [])
                    names.extend(n for n in categories.get(event_id, []) if n not in names)
        async for event_id, model in self.stream_isolated(linked, self._model, label=lambda i: f"event {i}"):
            pages[event_id] = model

        events: list[ScrapedEvent] = []
        for event_id, model in pages.items():
            try:
                parsed = parse_page(model, categories.get(event_id, []), offers, s, self.timezone)
            except Exception as exc:  # noqa: BLE001 - one odd page never drops the others
                self.record_failure(f"event {event_id}", exc)
                continue
            for event in parsed:
                if event.start_at is None or not is_current(
                    event.start_at, event.end_at, RetentionSettings(), self.timezone, time_known=event.time_known
                ):
                    continue  # undated or past: no event, and no redirect to resolve
                if event.extra.get("purchase_url") is None and (path := redirect_path(event, s)):
                    target = await self.optional(f"redirect {event.source_event_id}", self._redirect(path))
                    event.extra["purchase_url"] = target or f"https://{s.ticket_host}{path}"
                events.append(event)
        self.log.debug(
            "%d categories, %d event pages, %d coming performances", len(category_ids), len(pages), len(events)
        )
        for event in sorted(events, key=lambda e: (e.start_at or datetime.max.replace(tzinfo=UTC), e.source_event_id)):
            yield event  # nearest first

    async def _category(self, category_id: int) -> tuple[str, list[str]] | None:
        s = self.settings
        response = await self.optional(
            f"category {category_id}", self.request("GET", s.site_url + s.category_path.format(id=category_id))
        )
        if response is None:
            return None  # an unused id: 404
        name, ids = parse_category(response.text)
        return (name, ids) if name and ids else None

    async def _model(self, event_id: str) -> tuple[str, dict[str, Any]] | None:
        html = await self.get_text(self.settings.site_url + self.settings.event_path.format(id=event_id))
        model = page_model(html)
        return (event_id, model) if model is not None else None

    async def _offers(self) -> dict[tuple[str, str, str], dict[str, Any]]:
        s = self.settings
        body = await self.get_text(f"https://{s.ticket_host}{s.smarticket_shows_path}")
        return offers_index(parse_shows_body(body, s.ticket_host))

    async def _redirect(self, path: str) -> str | None:
        """The Smarticket page a ``redirect_event`` link lands on: the performance (``/iframe/event/<id>``), or
        the show's page (``/iframe/show/<id>``) for a date its API does not list; without the iframe prefix."""
        s = self.settings
        response = await self.request("GET", f"https://{s.ticket_host}{path}")
        match = re.search(r"/iframe(/(?:event|show)/\d+)", response.url.path)
        return f"https://{s.ticket_host}{match.group(1)}" if match else None


def redirect_path(event: ScrapedEvent, s: TzavtaSettings) -> str | None:
    """The box office's ``redirect_event`` path of a performance Smarticket's API does not list; None without a
    box-office code or a start (an undated item has no date to send)."""
    code = event.extra.get("tickets_code")
    if not code or event.start_at is None:
        return None
    start = event.start_at
    return s.redirect_path.format(code=code, date=start.date().isoformat(), hhmm=start.strftime("%H%M"))


def event_ids(html: str) -> list[str]:
    """The ``/event/<id>`` pages a page links, in its order."""
    return list(dict.fromkeys(_EVENT_LINK.findall(html or "")))


def parse_category(html: str) -> tuple[str | None, list[str]]:
    """A category page -> (its name, the event ids of its cards)."""
    tree = LexborHTMLParser(html)
    heading = tree.css_first("h1.list_title")
    name = clean_str(re.sub(r"\s+", " ", heading.text())) if heading else None
    ids = [
        m.group(1)
        for a in tree.css("a.shows_link")
        if (m := re.search(r"/event/(\d+)", a.attributes.get("href") or ""))
    ]
    return name, list(dict.fromkeys(ids))


def page_model(html: str) -> dict[str, Any] | None:
    """The event page's ``const model = {...}`` object; None when the page has none (a removed event)."""
    start = html.find(_MODEL)
    if start < 0:
        return None
    try:
        model, _ = json.JSONDecoder().raw_decode(html, start + len(_MODEL))
    except ValueError as exc:
        raise ParseError("event page: the model script is not JSON") from exc
    return model if isinstance(model, dict) else None


def performances(model: dict[str, Any]) -> list[dict[str, Any]]:
    shows = model.get("ShowsList") or ([model["MainShow"]] if isinstance(model.get("MainShow"), dict) else [])
    return [p for p in shows if isinstance(p, dict)]


def offers_index(shows: list[dict[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Smarticket's performances by (show id, ISO date, "HH:MM")."""
    return {
        (str(show.get("id")), str(p.get("show_date")), str(p.get("show_time") or "")[:5]): p
        for show, p in iter_performances(shows)
    }


def parse_page(
    model: dict[str, Any],
    categories: list[str],
    offers: dict[tuple[str, str, str], dict[str, Any]],
    s: TzavtaSettings,
    timezone: str,
) -> list[ScrapedEvent]:
    """One event page -> one event per performance (none for an umbrella page without dates)."""
    page_id = model.get("Id")
    title = clean_str(re.sub(r"\s+", " ", unescape(str(model.get("Title") or ""))))
    if not page_id or not title:
        return []
    texts = f"{model.get('Decription') or ''}\n{model.get('Editor') or ''}"
    description = html_to_text(f"{model.get('Subtitle') or ''}<br>{texts}")
    code = re.sub(r"\D", "", str(model.get("TicketsCode") or "")) or None
    external = ticket_link(texts, s) if model.get("ExternalSale") else None
    images = [
        f"{s.site_url}{s.image_path}{path}" for k in ("Image", "SecondImage") if (path := clean_str(model.get(k)))
    ]
    declared = declared_genres(categories, s)
    out: list[ScrapedEvent] = []
    for show in performances(model):
        start, time_known = _start(show, timezone)
        if start is None:
            continue  # no date: nothing to place it on
        place = clean_str(show.get("Show_Place")) or ""
        offer = offers.get((code or "", start.date().isoformat(), start.strftime("%H:%M"))) if code else None
        message = clean_str(offer.get("unavailability_msg")) if offer else None
        if message and _CANCELLED.search(message):
            continue
        sold_out = any(w in place.casefold() for w in s.sold_out_places) or bool(message and _SOLD_OUT.search(message))
        tickets = _tickets(offer, sold_out)
        prices = [t.price for t in tickets if t.price is not None]
        extra: dict[str, Any] = {"hall": None if sold_out else place or None, "show_id": show.get("Show_Id")}
        if code:
            extra["tickets_code"] = code
        if external:
            extra["purchase_url"] = external
        elif offer is not None:
            extra["purchase_url"] = f"https://{s.ticket_host}{s.smarticket_event_path.format(id=offer['id'])}"
        if flah := clean_str(model.get("Flah")):
            extra["flag"] = flah
        venue, address, city, lat, lng = _place(place, sold_out, s)
        out.append(
            ScrapedEvent(
                source=TzavtaScraper.name,
                source_event_id=f"{page_id}:{show.get('Show_Id') or start.strftime('%Y%m%d%H%M')}",
                url=f"{s.site_url}{s.event_path.format(id=page_id)}",
                title=title,
                description=description,
                start_at=start,
                end_at=_end(offer, start, timezone) if time_known else None,
                time_known=time_known,
                venue_name=venue,
                address=address,
                city=city,
                latitude=lat,
                longitude=lng,
                source_genres=declared,
                images=images,
                tickets=tickets,
                min_price=min(prices, default=None),
                max_price=max(prices, default=None),
                is_sold_out=sold_out,
                extra=extra,
            )
        )
    return out


def declared_genres(categories: list[str], s: TzavtaSettings) -> list[str]:
    """The event's categories (not the hall's own listing), each with its labels' words, lower case."""
    out: list[str] = []
    for name in categories:
        if name in s.hall_categories:
            continue
        for word in [name, *(w for key, words in s.labels.items() if key in name for w in words)]:
            if word.lower() not in out:
                out.append(word.lower())
    return out


def ticket_link(html: str, s: TzavtaSettings) -> str | None:
    """The first link in the texts to another site that is no profile or share: the box office of an
    ``ExternalSale`` show ("https://www.motystandup.com/event-details/...")."""
    for href in _HREF.findall(html or ""):
        url = unescape(href)
        host = (urlparse(url).hostname or "").lower().removeprefix("www.")
        if (
            not host
            or host.endswith("tzavta.co.il")
            or any(host == h or host.endswith("." + h) for h in s.social_hosts)
        ):
            continue
        return url
    return None


def _start(show: dict[str, Any], timezone: str) -> tuple[datetime | None, bool]:
    """(start, time known): None without a date; a date without a clock starts at midnight, time unknown."""
    try:
        day = date.fromisoformat(str(show.get("Tickets_Date") or "").strip())
    except ValueError:
        return None, False
    clock = _CLOCK.match(str(show.get("Show_Time") or "").strip())
    at = time(int(clock.group(1)), int(clock.group(2))) if clock else time.min
    return datetime.combine(day, at, tzinfo=zone(timezone)), bool(clock)


def _end(offer: dict[str, Any] | None, start: datetime | None, timezone: str) -> datetime | None:
    clock = _CLOCK.match(str((offer or {}).get("end_time") or "").strip())
    if not clock or start is None:
        return None
    end = datetime.combine(start.date(), time(int(clock.group(1)), int(clock.group(2))), tzinfo=zone(timezone))
    return end + timedelta(days=1) if end <= start else end


def _tickets(offer: dict[str, Any] | None, sold_out: bool) -> list[TicketTier]:
    status = TierStatus.SOLD_OUT if sold_out else TierStatus.ON_SALE
    tiers = []
    for p in (offer or {}).get("pricelist") or []:
        if not isinstance(p, dict):
            continue
        price = to_decimal(p.get("price"))
        tiers.append(
            TicketTier(
                source_ticket_id=str(p["id"]) if p.get("id") is not None else None,
                name=clean_str(p.get("name")) or "כרטיס",
                price=price,
                is_free=price == 0,
                status=status,
            )
        )
    return tiers


def _place(
    place: str, sold_out: bool, s: TzavtaSettings
) -> tuple[str, str | None, str | None, float | None, float | None]:
    """(venue, address, city, lat, lng): a configured place elsewhere, a hall named after a town (its own
    venue, geocoded), or else a hall of the building."""
    if not sold_out and (where := s.places.get(place)):
        return where.venue_name, where.address, where.city, where.latitude, where.longitude
    in_building = place in s.halls or place.startswith(s.venue_name)
    if not sold_out and place and not in_building and (town := _town(place)):
        return place, None, town, None, None
    return s.venue_name, s.address, s.city, s.latitude, s.longitude


def _town(text: str) -> str | None:
    hits = _localities().find(text)
    return hits[-1][1] if hits else None


@cache
def _localities() -> PlaceIndex:
    enrichment = get_settings().enrichment
    return PlaceIndex(all_cities(enrichment.cities, enrichment.places_min_population))
