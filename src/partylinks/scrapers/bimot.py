"""Bimot Global (bimotglobal.co.il, "בימות גלובל"; was bimot.co.il) - a Jerusalem producer of dance, music,
opera and theatre: Israeli companies (Kamea, the Kibbutz Contemporary Dance Company, Batsheva Ensemble) and
guest companies from abroad, mostly at the Israeli Opera in Tel Aviv and on tour (the Haifa Auditorium).

The old bimot.co.il no longer answers (time-outs direct, through the free Israeli proxies and our VPN, Nov 2026);
the company's site is now bimotglobal.co.il, WordPress + Elementor, which answers plain requests.

One wp-json call lists the current shows (``shows_path``, the ``show`` post type, without the archive term
``archive_season``: archived shows list no dates), one more names the show types (``types_path``: מחול,
מוזיקה, אופרה, מחזמר, קרקס, תאטרון, מופעי ילדים - the site's genres). Each show's page (one request per
show, ~5 in a season) holds its performances (``.show-date-entry``: dd/mm/yyyy, "weekday | HH:MM", the hall,
and the seller's link - the Israeli Opera box office on PRES, Barak for Haifa), the company line above the
title, the description and the artwork (og:image). The REST rows carry no content.

Halls are pinned in ``places`` (the hall as the site writes it -> venue, city, coordinates); an unpinned hall
keeps its own name as the venue and the address, with the city found in its name (``cities``). No prices
(they are on the sellers' pages). Every performance is also sold by its box office (`pres` opera, `barak`):
the seller link is the purchase_url, so dedup merges them.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import datetime
from html import unescape
from typing import Any

from pydantic import BaseModel, Field
from selectolax.lexbor import LexborHTMLParser

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, html_to_text, is_site_logo, og_image, read_clock, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings


class BimotPlace(BaseModel):
    venue_name: str
    city: str
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class BimotSettings(SourceSettings):
    site_url: str = "https://bimotglobal.co.il"
    shows_path: str = "/wp-json/wp/v2/show"
    types_path: str = "/wp-json/wp/v2/show_type?per_page=100&_fields=id,name"
    show_fields: str = "id,link,title,show_type,season"
    # The "previous seasons" term: every archived show is in it, and its pages list no dates.
    archive_season: int = 49
    per_page: int = 100
    max_pages: int = 5
    # Hall as the site writes it (spaces and dashes folded, see ``fold``) -> the venue.
    places: dict[str, BimotPlace] = Field(default_factory=dict)
    # Cities looked for in an unpinned hall's name ("היכל התרבות נתניה").
    cities: list[str] = Field(default_factory=lambda: ["תל אביב", "ירושלים", "חיפה", "באר שבע"])
    organizer: str = "בימות גלובל"
    slot_selector: str = ".show-date-entry"
    date_selector: str = ".show-date-date"
    clock_selector: str = ".show-date-details"
    hall_selector: str = ".ticket-notice"
    ticket_selector: str = "a[href]"
    description_selector: str = ".elementor-widget-text-editor"
    heading_selector: str = ".elementor-heading-title"


class BimotScraper(BaseScraper[BimotSettings]):
    name = "bimot"
    settings_model = BimotSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings

        async def page(index: int) -> tuple[list[dict[str, Any]], str | None]:
            params = {
                "per_page": s.per_page,
                "page": index + 1,
                "season_exclude": s.archive_season,
                "_fields": s.show_fields,
            }
            response = await self.request("GET", s.site_url + s.shows_path, params=params)
            rows = response.json()
            return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else [], response.headers.get(
                "X-WP-Total"
            )

        shows = await self.paginate(page, s.per_page, s.max_pages)
        types = await self.optional("show types", self.get_json(s.site_url + s.types_path, expect=list)) or []
        names = {t.get("id"): clean_str(unescape(str(t.get("name") or ""))) for t in types if isinstance(t, dict)}
        self.log.debug("listed %d current shows", len(shows))

        async def performances(show: dict[str, Any]) -> list[ScrapedEvent]:
            html = await self.get_text(str(show["link"]))
            return parse_show(show, html, names, s, self.timezone)

        events: list[ScrapedEvent] = []
        current = [r for r in shows if r.get("id") and r.get("link")]
        async for batch in self.stream_isolated(current, performances, lambda r: f"show {r.get('id')}"):
            events.extend(e for e in batch if is_current(e.start_at, e.end_at, RetentionSettings(), self.timezone))
        for event in sorted(events, key=lambda e: e.start_at or datetime.max.replace(tzinfo=zone(self.timezone))):
            yield event  # nearest first


def fold(value: str | None) -> str:
    """A hall name compared loosely: dashes as spaces, runs of spaces as one ("תל-אביב" = "תל אביב")."""
    return re.sub(r"\s+", " ", re.sub(r"[-–־]", " ", value or "")).strip()


def parse_show(
    show: dict[str, Any], html: str, types: dict[Any, str | None], s: BimotSettings, timezone: str
) -> list[ScrapedEvent]:
    """One show's page -> one event per dated performance."""
    tree = LexborHTMLParser(html)
    h1 = tree.css_first("h1")
    title = clean_str(h1.text()) if h1 else None
    title = title or clean_str(unescape(re.sub(r"<[^>]+>", " ", str((show.get("title") or {}).get("rendered") or ""))))
    if not title:
        return []
    company = _company(tree, s)
    text = tree.css_first(s.description_selector)
    description = html_to_text(text.html) if text else None
    image = og_image(html)
    images = [image] if image and not is_site_logo(image) else []
    genres = [g.lower() for i in show.get("show_type") or [] if (g := types.get(i))]
    places = {fold(k): v for k, v in s.places.items()}
    events = []
    for slot in tree.css(s.slot_selector):
        start, time_known = _start(slot, s, timezone)
        if start is None:
            continue
        hall_node = slot.css_first(s.hall_selector)
        hall = clean_str(hall_node.text()) if hall_node else None
        place = places.get(fold(hall))
        ticket = slot.css_first(s.ticket_selector)
        seller = clean_str(ticket.attributes.get("href")) if ticket else None
        extra: dict[str, Any] = {}
        if seller and seller.startswith(("http://", "https://")):
            extra["purchase_url"] = seller
        if hall and place and fold(hall) != fold(place.venue_name):
            extra["hall"] = hall
        if company:
            extra["performer"] = company
        city = place.city if place else _city(hall, s)
        events.append(
            ScrapedEvent(
                source=BimotScraper.name,
                source_event_id=f"{show['id']}-{start:%Y%m%d%H%M}",
                url=str(show["link"]),
                title=title,
                description=description,
                start_at=start,
                time_known=time_known,
                venue_name=place.venue_name if place else hall,
                address=(place.address if place else None)
                or (f"{hall}, {city}" if hall and city and city not in hall else hall),
                city=city,
                latitude=place.latitude if place else None,
                longitude=place.longitude if place else None,
                source_genres=list(dict.fromkeys(genres)),
                images=images,
                organizer=s.organizer,
                extra=extra,
            )
        )
    return events


def _start(slot: Any, s: BimotSettings, timezone: str) -> tuple[datetime | None, bool]:
    """The performance's start from "29/04/2027" and "חמישי | 21:00"; midnight and False without a time."""
    date_node = slot.css_first(s.date_selector)
    match = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", date_node.text() if date_node else "")
    if not match:
        return None, False
    day, month, year = (int(x) for x in match.groups())
    clock_node = slot.css_first(s.clock_selector)
    clock = read_clock(m.group(0)) if clock_node and (m := re.search(r"\d{1,2}:\d{2}", clock_node.text())) else None
    try:
        start = datetime(
            year, month, day, clock.hour if clock else 0, clock.minute if clock else 0, tzinfo=zone(timezone)
        )
    except ValueError:
        return None, False
    return start, clock is not None


def _company(tree: LexborHTMLParser, s: BimotSettings) -> str | None:
    """The company line written above the title (the last heading before the h1)."""
    company = None
    for node in tree.css(f"{s.heading_selector}, h1"):
        if node.tag == "h1":
            return company
        company = clean_str(node.text()) or company
    return None


def _city(hall: str | None, s: BimotSettings) -> str | None:
    folded = fold(hall)
    return next((c for c in s.cities if fold(c) in folded), None)
