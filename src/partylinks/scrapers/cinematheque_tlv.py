"""Tel Aviv Cinematheque (cinema.co.il, "סינמטק תל אביב") - its PRES box office.

The WordPress site sells through PRES (cintlv.pres.global, redirecting to cintlv.presglobal.store),
the same JSON API as the ``pres`` and ``cinemacity`` sources:

* ``GET /api/presentations/`` - every screening in one call (~630 rows, ~0.6 MB, old VOD rows
  included): id, local ``dateTime``, ``businessDate``, feature id/name/category/duration, hall
  (``venueName``: "אולם 1".."אולם 6", "ספריה"), ``languageISO``, ``soldout``.
* ``GET /api/features/`` - every feature's synopsis HTML and ``imageData`` (poster; ~15% have
  none). Optional: without it events keep everything but synopsis and poster.
* ``GET /api/presentations/<id>/ticketGroups`` - prices, read once per feature from its first
  screening in the window and applied to all its days, ``price_batch`` movie-days at a time in date
  order (the whole feed: ~370 features, Nov 2026), so the soonest are yielded first. Optional.

One event per (feature, business day) within ``days_ahead`` days (Tel Aviv: the whole feed, ~8 months
ahead); ``start_at`` is that day's first screening, every time is listed in the description and
``extra.showtimes``; an event whose day is over is not yielded (``is_current``). The site's
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
A card with no order button is an event the box office does not sell, mostly free entry (Solidarity 2026:
"כניסה חופשית – יום ה׳ 10.12, 15:00, אולם 6"): it is an event of its own, its day from the card's date
heading and its hour from the card's text, or from its own site page when the card's text is cut short.
"""

from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, HttpUrl
from selectolax.lexbor import LexborHTMLParser

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import ParseError, clean_str, html_to_text, og_image, parse_iso, to_int, zone
from partylinks.retention import is_current
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
# A card without an order button: its post id ("event_id-119841"), date heading ("10.12, חמישי"), length
# ("אורך:180"), hall and free entry.
_CARD_ID = re.compile(r"\bevent_id-(\d+)")
_CARD_DAY = re.compile(r"\b(\d{1,2})[./](\d{1,2})\b")
_MINUTES = re.compile(r"אורך:\s*(\d+)")
_HALL = re.compile(r"אולם\s*\d+")
_FREE = re.compile(r"כניסה חופשית|free entrance", re.IGNORECASE)
_WEEKDAYS = {"שני": 0, "שלישי": 1, "רביעי": 2, "חמישי": 3, "שישי": 4, "שבת": 5, "ראשון": 6}


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
    price_batch: int = Field(40, ge=1)  # movie-days whose features' prices are fetched before they are yielded
    default_runtime_minutes: int = Field(120, ge=1)  # a screening without durationInMinutes
    branch_id: str = "tlv"
    venue_name: str = "סינמטק תל אביב"
    address: str | None = "הארבעה 5"
    city: str | None = "תל אביב"
    festivals: list[CinemathequeFestival] = Field(default_factory=list)
    # PRES category name (featureCategoryName) -> the event's source genres; the rest stay "קולנוע".
    category_genres: dict[str, list[str]] = Field(default_factory=dict)
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
        by_id = {clean_str(f.get("id")): f for f in features or [] if isinstance(f, dict)}
        cards: dict[str, FestivalCard] = {}
        free_cards: dict[str, FreeCard] = {}
        for festival in s.festivals:
            sold, free = await self.optional(f"festival {festival.page_url}", self.festival_cards(festival)) or ({}, {})
            cards.update(sold)
            free_cards.update(free)
        retention = RetentionSettings()

        def current(event: ScrapedEvent) -> bool:  # today's past screenings too
            return is_current(event.start_at, event.end_at, retention, self.timezone, time_known=event.time_known)

        unsold: list[ScrapedEvent] = []  # festival events PRES does not sell (free entry), soonest first
        for card in free_cards.values():
            event = build_free_event(card, today, s, self.name, self.timezone)
            if event and not event.time_known and card.url:  # the card's text cut short before the hour
                page = await self.optional(f"festival event {card.url}", self.get_text(card.url))
                event = build_free_event(card, today, s, self.name, self.timezone, page) or event
            if event and current(event):
                unsold.append(event)
        unsold.sort(key=_start)

        # Prices from each feature's first screening, fetched for one batch of movie-days at a time in date
        # order: the whole feed is ~370 features (~0.75 s a call), and a run cut half way must hold this week.
        first: dict[str, Any] = {}

        async def prices(movie: str) -> tuple[str, list[TicketTier]]:
            url = s.api_url + s.ticket_groups_path.format(id=first[movie])
            return movie, parse_ticket_groups(await self.get_json(url, expect=dict))

        tiers: dict[str, list[TicketTier]] = {}
        days = list(groups.items())
        for at in range(0, len(days), s.price_batch):
            batch = days[at : at + s.price_batch]
            new = [movie for movie in dict.fromkeys(movie for (movie, _day), _shows in batch) if movie not in first]
            for (movie, _day), shows in batch:
                first.setdefault(movie, shows[0][1].get("id"))
            tiers.update([x async for x in self.stream_isolated(new, prices, lambda m: f"prices of feature {m}")])
            for (movie, day), shows in batch:
                event = build_event(movie, day, shows, by_id.get(movie) or {}, tiers.get(movie, []), s, self.name)
                event = apply_festival(event, cards)
                while unsold and _start(unsold[0]) <= _start(event):
                    yield unsold.pop(0)
                if current(event):
                    yield event
        for event in unsold:
            yield event

    async def festival_cards(
        self, festival: CinemathequeFestival
    ) -> tuple[dict[str, FestivalCard], dict[str, FreeCard]]:
        """PRES screening id -> its festival card, and the cards no PRES screening sells (by post id), each with
        the sections it is listed under."""
        s = self.settings
        main, sections = festival_sections(await self.get_text(festival.page_url))
        if not main:
            raise ParseError("festival page without its category filter")
        cards: dict[str, FestivalCard] = {}
        free: dict[str, FreeCard] = {}
        for category in [main, *sections]:
            for paged in range(1, s.max_festival_pages + 1):
                form = {"action": s.festival_action, "paged": str(paged), "category_id": category}
                html = (await self.request("POST", s.ajax_url, data=form)).text
                page, listed = parse_festival_cards(html), parse_free_cards(html)
                if not page and not listed:  # a page of free cards only is not the end
                    break
                for order_id, card in page.items():
                    cards.setdefault(order_id, card)
                for post_id, free_card in listed.items():
                    free.setdefault(post_id, free_card)
                if category in sections:
                    for known in [*(cards[k] for k in page), *(free[k] for k in listed)]:
                        known.sections.append(sections[category])
        for card in [*cards.values(), *free.values()]:
            card.genres = list(dict.fromkeys(
                [*festival.genres, *(festival.section_genres[n] for n in card.sections if n in festival.section_genres)]
            ))  # fmt: skip
        self.log.debug("festival %s: %d screenings, %d free events", festival.page_url, len(cards), len(free))
        return cards, free


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
        stored: dict[str, list[str]] = {}  # movie id -> its stored picture (event ids are "movie:branch:day")
        for event_id, images in self.known_images.items():
            stored.setdefault(event_id.split(":", 1)[0], images)
        missing: dict[str, list[ScrapedEvent]] = defaultdict(list)
        async for event in super().scrape():  # yielded as they come, soonest first: a cut run keeps this week
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


class FreeCard(FestivalCard):
    """A festival card with no PRES order button: an event of its own (free entry, "כניסה חופשית")."""

    post_id: str
    title: str
    day: str  # the date heading: "10.12, חמישי"
    text: str = ""  # the card's paragraph: "סולידריות עכשיו כניסה חופשית – יום ה׳ 10.12, 15:00, אולם 6"
    minutes: int | None = None  # "אורך:180"


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
            image=_artwork(image),
        )
    return cards


def _artwork(url: str | None) -> str | None:
    """A card's picture: not the lazy-load data: placeholder, nor the theme's own "no picture" image
    (wp-content/themes/.../event_d2_new.png on free-entry cards)."""
    return url if url and url.startswith("http") and "/wp-content/themes/" not in url else None


def parse_free_cards(cards_html: str) -> dict[str, FreeCard]:
    """One page of festival cards: the cards with no PRES order button, by their post id."""
    cards: dict[str, FreeCard] = {}
    for box in LexborHTMLParser(cards_html).css("div.alm-listing"):
        post = _CARD_ID.search(box.attributes.get("class") or "")
        if box.css_first("a.order-btn") or not post:
            continue
        link, heading = box.css_first(".title a"), box.css_first(".by-date-title")
        href = clean_str(link.attributes.get("href")) if link else None
        title = clean_str(link.text(strip=True)) if link else None
        day = clean_str(heading.text(strip=True)) if heading else None
        if not (href and href.startswith("http") and title and day):
            continue
        img, length, paragraph = (
            box.css_first(".img-wraper img"),
            box.css_first(".title p"),
            box.css_first(".paragraph"),
        )
        minutes = _MINUTES.search(length.text() if length else "")
        cards[post.group(1)] = FreeCard(
            post_id=post.group(1),
            url=href,
            image=_artwork((img.attributes.get("data-src") or img.attributes.get("src")) if img else None),
            title=title,
            day=day,
            text=html_to_text(paragraph.html) or "" if paragraph else "",
            minutes=int(minutes.group(1)) if minutes else None,
        )
    return cards


def build_free_event(
    card: FreeCard, today: date, s: TelAvivCinemathequeSettings, source: str, timezone: str,
    page_html: str | None = None,
) -> ScrapedEvent | None:  # fmt: skip
    """A free card's event: the day from its heading (the year nearest ``today`` on that weekday), the hour
    from its text ("... 10.12, 15:00, אולם 6") or else from its own page; no hour found: all-day."""
    m = _CARD_DAY.search(card.day)
    if not m:
        return None
    weekday = next((n for name, n in _WEEKDAYS.items() if name in card.day.split(",", 1)[-1].split()), None)
    days = []
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            days.append(date(year, int(m.group(2)), int(m.group(1))))
        except ValueError:
            continue
    days = [d for d in days if weekday is None or d.weekday() == weekday] or days
    if not days:
        return None
    day = min(days, key=lambda d: abs((d - today).days))
    page = html_to_text(page_html) or "" if page_html else ""
    hour = re.compile(rf"\b0?{day.day}[./]0?{day.month}(?:[./]\d{{2,4}})?\s*,\s*(\d{{1,2}}):(\d{{2}})\b")
    found = hour.search(card.text) or hour.search(page)
    hh, mm = (int(found.group(1)), int(found.group(2))) if found else (0, 0)
    start = datetime(day.year, day.month, day.day, hh, mm, tzinfo=zone(timezone))
    free = bool(_FREE.search(card.text) or _FREE.search(page))
    hall = _HALL.search(card.text) or _HALL.search(page)
    extra = {"festival_card": card.post_id, "series": card.sections, "halls": [hall.group(0)] if hall else []}
    return ScrapedEvent(
        source=source,
        source_event_id=f"card{card.post_id}:{s.branch_id}:{day.isoformat()}",
        url=HttpUrl(card.url or ""),
        title=card.title,
        description=card.text or None,
        start_at=start,
        end_at=start + timedelta(minutes=card.minutes) if found and card.minutes else None,
        time_known=bool(found),
        venue_name=s.venue_name,
        address=s.address,
        city=s.city,
        source_genres=card.genres,
        image_url=card.image,
        images=[card.image] if card.image else [],
        min_price=Decimal(0) if free else None,
        max_price=Decimal(0) if free else None,
        tickets=[TicketTier(name="כניסה חופשית", price=Decimal(0), is_free=True)] if free else [],
        extra={k: v for k, v in extra.items() if v not in (None, [])},
    )


def _start(event: ScrapedEvent) -> datetime:
    """The order events are yielded in (every event here has a start)."""
    return event.start_at or datetime.max.replace(tzinfo=UTC)


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
    categories = [c.strip() for c in (category or "").split(",") if c.strip()]
    mapped = list(dict.fromkeys(g for c in categories for g in s.category_genres.get(c, [])))
    extra = {
        "showtimes": [t.isoformat() for t in times],
        "movie_id": movie,
        "branch_id": s.branch_id,
        "purchase_url": s.order_url.format(id=first.get("id")),
        "screening_ids": [r.get("id") for _, r in shows],
        "original_title": clean_str(first.get("featureAdditionalName")),
        "runtime_minutes": to_int(first.get("durationInMinutes")),
        "language": clean_str(first.get("languageISO")) or clean_str(feature.get("originalLanguageISO")),
        "series": [c for c in categories if c != "כללי"],
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
        # Series names ("פסטיבל גאה ריכוז") would map to wrong categories: kept in extra only, unless
        # category_genres names the category ("קורסים" -> הרצאה, "מטרופוליטן" -> קולנוע + אופרה).
        source_genres=["הרצאה"] if course else mapped or ["קולנוע"],
        image_url=clean_str(feature.get("imageData")),
        min_price=min(priced, default=None),
        max_price=max(priced, default=None),
        tickets=tiers,
        is_sold_out=all(r.get("soldout") for _, r in shows),
        extra={k: v for k, v in extra.items() if v not in (None, [])},
    )
