"""Tel Aviv Cinematheque (cinema.co.il, "סינמטק תל אביב") - its PRES box office.

The WordPress site sells through PRES (cintlv.pres.global, redirecting to cintlv.presglobal.store),
the same JSON API as the ``pres`` and ``cinemacity`` sources:

* ``GET /api/presentations/`` - every screening in one call (~450 rows, ~0.6 MB, old VOD rows
  included): id, local ``dateTime``, ``businessDate``, feature id/name/category/duration, hall
  (``venueName``: "אולם 1".."אולם 6", "ספריה"), ``languageISO``, ``soldout``.
* ``GET /api/features/`` - every feature's synopsis HTML and ``imageData`` (poster; ~15% have
  none). Optional: without it events keep everything but synopsis and poster.
* ``GET /api/presentations/<id>/ticketGroups`` - prices, read once per feature from its first
  screening in the window (~80 requests a week) and applied to all its days. Optional.

One event per (feature, business day) within ``days_ahead`` days; ``start_at`` is that day's
first screening, every time is listed in the description and ``extra.showtimes``. The site's
event pages are WordPress slugs the API does not give, so ``url`` is the PRES order page of the
first screening. Film + talk screenings ("+ הרצאה") stay films; lecture-course meetings
("... - מפגש 7", "2026-2027" course categories) are tagged ``הרצאה`` instead of ``קולנוע``.
robots.txt of www allows everything but /wp-admin (admin-ajax.php is allowed); the PRES host has none.

Festivals (``festivals`` in config, e.g. Soundtrack Tel Aviv): the festival's page on www lists every
screening as a card with its PRES order id, artwork and site event page. The page itself gives the
section filters ("פאנק", "ילדים"...); the cards come from the theme's own ``load_more_posts`` admin-ajax
call, paged until empty, once for the festival and once per section (~20 requests). A screening whose id
is on a card gets the card's artwork (PRES has almost none), its site page as ``url``, the festival's
genres and the mapped section genres. Optional: without it the PRES events are saved as they are.
"""

from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field
from selectolax.lexbor import LexborHTMLParser

from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import ParseError, clean_str, html_to_text, og_image, parse_iso, to_int, zone
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.pres import parse_ticket_groups

# A lecture course: a numbered meeting, or a season category ("... 2026-2027", truncated "2026-20").
_COURSE_TITLE = re.compile(r"מפגש\s*\d+")
_COURSE_CATEGORY = re.compile(r"\b20\d\d-20")
# jer-cin.org.il's calendar: <button ... data-event-id="43087" data-event-node="99048">.
_FILM_BUTTON = re.compile(r'data-event-id="(\d+)"\s+data-event-node="(\d+)"')
# A festival card's order button: https://cintlv.pres.global/order/135449 (a PRES screening id).
_ORDER_ID = re.compile(r"/order/(\d+)")
_SECTION = re.compile(r"movie-cat-(\d+)")


class CinemathequeFestival(BaseModel):
    """A festival page on the cinematheque's site, its cards matched to PRES screenings by order id."""

    page_url: str
    genres: list[str] = Field(default_factory=lambda: ["פסטיבל"])  # source genres of every screening
    # Section name -> source genre. Sections not listed stay in extra.series only ("פאנק" alone would map
    # to funk, "ישראלי" to Israeli music).
    section_genres: dict[str, str] = Field(default_factory=dict)


class TelAvivCinemathequeSettings(SourceSettings):
    api_url: str
    presentations_path: str
    features_path: str
    ticket_groups_path: str  # formatted with ``id`` (a presentation id)
    order_url: str  # formatted with ``id``
    days_ahead: int = Field(7, ge=1)
    default_runtime_minutes: int = Field(120, ge=1)  # a screening without durationInMinutes
    branch_id: str = "tlv"
    venue_name: str = "סינמטק תל אביב"
    address: str | None = "הארבעה 5"
    city: str | None = "תל אביב"
    festivals: list[CinemathequeFestival] = Field(default_factory=list)
    ajax_url: str = "https://www.cinema.co.il/wp-admin/admin-ajax.php"
    festival_action: str = "load_more_posts"  # the festival page's own "load more" call: paged cards
    max_festival_pages: int = Field(50, ge=1)  # a guard against a site that never answers empty


class TelAvivCinemathequeScraper(BaseScraper[TelAvivCinemathequeSettings]):
    name = "cinematheque_tlv"
    settings_model = TelAvivCinemathequeSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        listing = await self.get_json(s.api_url + s.presentations_path, expect=dict)
        features = await self.optional("features", self.get_json(s.api_url + s.features_path, expect=list))
        today = datetime.now(zone(self.timezone)).date()
        groups = group_screenings(listing, self.timezone, today, s.days_ahead)
        self.log.debug("listed %d movie-days", len(groups))

        # Prices from each feature's first screening in the window.
        first: dict[str, Any] = {}
        for (movie, _), shows in groups.items():
            first.setdefault(movie, shows[0][1].get("id"))

        async def prices(movie: str) -> tuple[str, list[TicketTier]]:
            url = s.api_url + s.ticket_groups_path.format(id=first[movie])
            return movie, parse_ticket_groups(await self.get_json(url, expect=dict))

        tiers = dict([x async for x in self.stream_isolated(first, prices, lambda m: f"prices of feature {m}")])
        by_id = {clean_str(f.get("id")): f for f in features or [] if isinstance(f, dict)}
        cards: dict[str, FestivalCard] = {}
        for festival in s.festivals:
            cards.update(await self.optional(f"festival {festival.page_url}", self.festival_cards(festival)) or {})
        for (movie, day), shows in groups.items():
            event = build_event(movie, day, shows, by_id.get(movie) or {}, tiers.get(movie, []), s, self.name)
            yield apply_festival(event, cards)

    async def festival_cards(self, festival: CinemathequeFestival) -> dict[str, FestivalCard]:
        """PRES screening id -> its festival card, with the sections it is listed under."""
        s = self.settings
        main, sections = festival_sections(await self.get_text(festival.page_url))
        if not main:
            raise ParseError("festival page without its category filter")
        cards: dict[str, FestivalCard] = {}
        for category in [main, *sections]:
            for paged in range(1, s.max_festival_pages + 1):
                form = {"action": s.festival_action, "paged": str(paged), "category_id": category}
                page = parse_festival_cards((await self.request("POST", s.ajax_url, data=form)).text)
                if not page:
                    break
                for order_id, card in page.items():
                    known = cards.setdefault(order_id, card)
                    if category in sections:
                        known.sections.append(sections[category])
        for card in cards.values():
            card.genres = list(dict.fromkeys(
                [*festival.genres, *(festival.section_genres[n] for n in card.sections if n in festival.section_genres)]
            ))  # fmt: skip
        self.log.debug("festival %s: %d screenings", festival.page_url, len(cards))
        return cards


class JerusalemCinemathequeSettings(TelAvivCinemathequeSettings):
    # The cinematheque's own site: its films' pictures (PRES gives a poster for ~1 feature in 150).
    site_url: str = "https://www.jer-cin.org.il"
    calendar_path: str = "/he"  # buttons "data-event-id" (a PRES screening) + "data-event-node" (its film page)
    film_path: str = "/he/event/{node}"  # og:image: a still of the film
    # robots.txt asks for Crawl-delay 180: one site request every this many seconds, this source only.
    crawl_delay_seconds: float = Field(180, ge=0)
    # Films whose picture is fetched per run (each waits crawl_delay_seconds); the rest follow next runs.
    max_pictures_per_run: int = Field(15, ge=0)


class JerusalemCinemathequeScraper(TelAvivCinemathequeScraper):
    """Jerusalem Cinematheque (jer-cin.org.il): its box office is PRES too (jer-cin.pres.global).

    PRES has almost no posters, so each film's picture comes from the cinematheque's own film page, at the
    site's Crawl-delay (180 s): every event is saved first, then up to ``max_pictures_per_run`` films get their
    picture (re-yielded with it); a film with a stored picture is never fetched again. Venue in settings.
    """

    name = "cinematheque_jlm"
    settings_model = JerusalemCinemathequeSettings  # type: ignore[assignment]

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        assert isinstance(s, JerusalemCinemathequeSettings)
        events = [event async for event in super().scrape()]
        stored: dict[str, list[str]] = {}  # movie id -> its stored picture (event ids are "movie:branch:day")
        for event_id, images in self.known_images.items():
            stored.setdefault(event_id.split(":", 1)[0], images)
        missing: dict[str, list[ScrapedEvent]] = defaultdict(list)
        for event in events:
            movie = str(event.extra.get("movie_id") or "")
            if not event.image_url and (known := stored.get(movie)):
                event.image_url, event.images = known[0], known
            if not event.image_url and movie:
                missing[movie].append(event)
            yield event
        if not missing or not s.max_pictures_per_run:
            return
        calendar = await self.optional("calendar", self.get_text(s.site_url + s.calendar_path)) or ""
        nodes = film_nodes(calendar)
        fetched = 0
        for movie, movie_events in missing.items():
            screenings = [str(i) for e in movie_events for i in e.extra.get("screening_ids") or []]
            node = next((nodes[i] for i in screenings if i in nodes), None)
            if node is None:
                continue  # not on the calendar page yet: a later run
            if fetched >= s.max_pictures_per_run:
                break
            await asyncio.sleep(s.crawl_delay_seconds)  # robots.txt: Crawl-delay 180, after the calendar too
            page = await self.optional(f"film {node}", self.get_text(s.site_url + s.film_path.format(node=node)))
            fetched += 1
            if image := og_image(page or ""):
                for event in movie_events:
                    event.image_url, event.images = image, [image]
                    yield event  # saved again, now with its picture


class FestivalCard(BaseModel):
    url: str | None = None
    image: str | None = None
    sections: list[str] = Field(default_factory=list)
    genres: list[str] = Field(default_factory=list)


def festival_sections(page_html: str) -> tuple[str | None, dict[str, str]]:
    """The festival page's filters: its own category id (the active one) and section id -> name."""
    tree = LexborHTMLParser(page_html)
    main, sections = None, {}
    for li in tree.css("li[data-filters]"):
        m = _SECTION.fullmatch(li.attributes.get("data-filters") or "")
        name = clean_str(li.text(strip=True))
        if not m or not name:
            continue
        if "my-active" in (li.attributes.get("class") or "").split():
            main = main or m.group(1)
        else:
            sections[m.group(1)] = name
    if main:
        sections.pop(main, None)
    return main, sections


def parse_festival_cards(cards_html: str) -> dict[str, FestivalCard]:
    """One page of festival cards: PRES order id -> its site page and artwork."""
    cards: dict[str, FestivalCard] = {}
    for box in LexborHTMLParser(cards_html).css("div.alm-listing"):
        order = box.css_first("a.order-btn")
        m = _ORDER_ID.search(order.attributes.get("href") or "") if order else None
        if not m:
            continue
        link = box.css_first(".title a")
        img = box.css_first(".img-wraper img")
        image = img and (img.attributes.get("data-src") or img.attributes.get("src"))
        cards[m.group(1)] = FestivalCard(
            url=clean_str(link.attributes.get("href")) if link else None,
            image=image if image and image.startswith("http") else None,  # not the lazy-load data: placeholder
        )
    return cards


def apply_festival(event: ScrapedEvent, cards: dict[str, FestivalCard]) -> ScrapedEvent:
    """The festival card of any of the event's screenings: its page, artwork, sections and genres."""
    card = next((c for i in event.extra.get("screening_ids") or [] if (c := cards.get(str(i)))), None)
    if card is None:
        return event
    if card.url:
        event.url = card.url
    if card.image and not event.image_url:
        event.image_url, event.images = card.image, [card.image]
    event.source_genres = list(dict.fromkeys([*event.source_genres, *card.genres]))
    if card.sections:
        event.extra["series"] = list(dict.fromkeys([*event.extra.get("series", []), *card.sections]))
    return event


def film_nodes(calendar_html: str) -> dict[str, str]:
    """The calendar's buttons: PRES screening id -> the film page's node id."""
    return dict(_FILM_BUTTON.findall(calendar_html))


def group_screenings(
    listing: dict[str, Any], timezone: str, today: date, days_ahead: int
) -> dict[tuple[str, date], list[tuple[datetime, dict[str, Any]]]]:
    """Public screenings per (feature id, business day) within the window, soonest first."""
    rows = listing.get("presentations")
    if not isinstance(rows, list):
        raise ParseError("listing without presentations")
    last_day = today + timedelta(days=days_ahead - 1)
    groups: dict[tuple[str, date], list[tuple[datetime, dict[str, Any]]]] = defaultdict(list)
    for row in rows:
        if not isinstance(row, dict) or row.get("private"):
            continue
        start, movie = parse_iso(row.get("dateTime"), timezone), clean_str(row.get("featureId"))
        if not start or not movie:
            continue
        try:
            day = date.fromisoformat(str(row.get("businessDate")))  # a 00:15 show belongs to the evening before
        except ValueError:
            day = start.date()
        if today <= day <= last_day:
            groups[(movie, day)].append((start, row))
    for shows in groups.values():
        shows.sort(key=lambda x: x[0])
    # The API lists rows by feature, not by time (Nov 2026): the soonest day first, also for the price calls.
    return dict(sorted(groups.items(), key=lambda g: g[1][0][0]))


def build_event(
    movie: str, day: date, shows: list[tuple[datetime, dict[str, Any]]], feature: dict[str, Any],
    tiers: list[TicketTier], s: TelAvivCinemathequeSettings, source: str = "cinematheque_tlv",
) -> ScrapedEvent:  # fmt: skip
    first = shows[0][1]
    times = list(dict.fromkeys(start for start, _ in shows))
    title = clean_str(first.get("featureName")) or clean_str(feature.get("name")) or movie
    category = clean_str(first.get("featureCategoryName")) or clean_str(feature.get("categoryName"))
    course = bool(_COURSE_TITLE.search(title) or _COURSE_CATEGORY.search(category or ""))
    lines = [
        html_to_text(clean_str(feature.get("synopsis"))),
        "הקרנות: " + ", ".join(t.strftime("%H:%M") for t in times),
    ]
    priced = [t.price for t in tiers if t.price is not None]
    extra = {
        "showtimes": [t.isoformat() for t in times],
        "movie_id": movie,
        "branch_id": s.branch_id,
        "purchase_url": s.order_url.format(id=first.get("id")),
        "screening_ids": [r.get("id") for _, r in shows],
        "original_title": clean_str(first.get("featureAdditionalName")),
        "runtime_minutes": to_int(first.get("durationInMinutes")),
        "language": clean_str(first.get("languageISO")) or clean_str(feature.get("originalLanguageISO")),
        "series": [c.strip() for c in (category or "").split(",") if c.strip() and c.strip() != "כללי"],
        "halls": list(dict.fromkeys(h for _, r in shows if (h := clean_str(r.get("venueName"))))),
        "trailer": clean_str(feature.get("trailer")),
    }
    return ScrapedEvent(
        source=source,
        source_event_id=f"{movie}:{s.branch_id}:{day.isoformat()}",
        url=extra["purchase_url"],
        title=title,
        description="\n\n".join(line for line in lines if line),
        start_at=times[0],
        # Until the last screening ends: with no end, the day's listing is over 3 h after its first one.
        end_at=times[-1] + timedelta(minutes=to_int(first.get("durationInMinutes")) or s.default_runtime_minutes),
        venue_name=s.venue_name,
        address=s.address,
        city=s.city,
        # Series names ("פסטיבל גאה ריכוז") would map to wrong categories: kept in extra only.
        source_genres=["הרצאה"] if course else ["קולנוע"],
        image_url=clean_str(feature.get("imageData")),
        min_price=min(priced, default=None),
        max_price=max(priced, default=None),
        tickets=tiers,
        is_sold_out=all(r.get("soldout") for _, r in shows),
        extra={k: v for k, v in extra.items() if v not in (None, [])},
    )
