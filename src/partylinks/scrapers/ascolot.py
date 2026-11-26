"""Askolot (extra-academic.openu.ac.il/sites/ascolot, "אסכולות", the Open University's school of enrichment
studies): lecture series, concert series, film clubs and tours in Tel Aviv, Raanana, Jerusalem, Haifa and online.

The site is SharePoint. One listing call (``listing_path`` with ``pageSize`` over the catalogue, ~77 courses:
every region and category filter lists the same courses) gives each course's page, title and artwork (the
card's picture; the course page has only the lecturer's photo). Each course page (one request) holds its
category (the breadcrumb), the text and one block per group (``.course-info-timeslot``: a region and a hall):
the hall, the weekday and hours ("1 מפגשים:ימי שני, 17:30-19:00"), the prices, the registration link and the
meeting days ("2026:" "27 נובמבר, 25 דצמבר" "2027:" "22 ינואר, ...").

Almost every course is a series sold whole ("מחיר מלא/ תלמיד חדש באסכולות - 865 ₪" for 8 meetings): no event,
the same rule as every source (ZOA's and zoatlv's "קורס אסכולות" skip). A group is an event when it is one
meeting ("מפגש חד פעמי", "מופע חד פעמי") or it sells a ticket per meeting ("מחיר כרטיס - 130 ₪"): one event per
day. Online groups ("מקוון") and tours ("סיור": a meeting point per tour, sold as a series) are no events. The
halls are mapped to a building, a city and a pin in config (``venues``); a hall with no entry keeps its name.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urljoin

from pydantic import BaseModel, Field
from selectolax.lexbor import LexborHTMLParser, LexborNode

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import clean_str, html_to_text, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_COURSE = re.compile(r"/Course/([0-9a-fA-F-]{36})\.aspx", re.IGNORECASE)
_HOURS = re.compile(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?:\s*[-–]\s*([01]?\d|2[0-3]):([0-5]\d))?(?!\d)")
_YEAR = re.compile(r"(20\d\d)\s*:")
_DAY = re.compile(r"(\d{1,2})\s+([א-ת]+)")
_AMOUNT = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+)\s*₪")
_MONTHS = {
    "ינואר": 1,
    "פברואר": 2,
    "מרץ": 3,
    "מרס": 3,
    "אפריל": 4,
    "מאי": 5,
    "יוני": 6,
    "יולי": 7,
    "אוגוסט": 8,
    "ספטמבר": 9,
    "אוקטובר": 10,
    "נובמבר": 11,
    "דצמבר": 12,
}


class AscolotVenue(BaseModel):
    venue_name: str
    city: str
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class AscolotSettings(SourceSettings):
    site_url: str = "https://extra-academic.openu.ac.il"
    listing_path: str = "/sites/ascolot/allcourses/coursefilter/Pages/default.aspx"
    page_size: int = 1000  # over the whole catalogue (~77): one listing call
    # The price label of a ticket per meeting; any other price is the whole series'.
    ticket_label: str = "מחיר כרטיס"
    ticket_name: str = "כרטיס"
    # A group whose hall line starts with one of these is no event: online, a tour's meeting points.
    skip_places: list[str] = Field(default_factory=lambda: ["מקוון", "סיור"])
    # The hall line's seating note: not part of the hall's name.
    seating_notes: list[str] = Field(default_factory=lambda: ["ללא מקומות מסומנים"])
    # The hall line before " - <hall>" (the building) -> its name, city and pin.
    venues: dict[str, AscolotVenue] = Field(default_factory=dict)
    organizer: str = "אסכולות - האוניברסיטה הפתוחה"
    placeholder_images: list[str] = Field(default_factory=lambda: ["white128x128"])


class AscolotScraper(BaseScraper[AscolotSettings]):
    name = "ascolot"
    settings_model = AscolotSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        listing = await self.get_text(s.site_url + s.listing_path, params={"pageSize": s.page_size, "pageIndex": 0})
        courses = parse_listing(listing, s)
        self.log.debug("listed %d courses", len(courses))
        retention = RetentionSettings()

        async def read(course: dict[str, Any]) -> list[ScrapedEvent]:
            page = await self.get_text(course["url"])
            return parse_course(page, course, s, self.timezone)

        found: list[ScrapedEvent] = []
        async for events in self.stream_isolated(courses, read, label=lambda c: c["url"]):
            found += [
                e
                for e in events or []
                if is_current(e.start_at, e.end_at, retention, self.timezone, time_known=e.time_known)
            ]
        # The listing has no dates: the few one-off meetings are yielded nearest first once all are read.
        found.sort(key=lambda e: e.start_at or datetime.max.replace(tzinfo=zone(self.timezone)))
        for event in found:
            yield event


def parse_listing(page_html: str, s: AscolotSettings) -> list[dict[str, Any]]:
    """The courses on the listing page: page URL, id, card title and artwork, once each."""
    root = LexborHTMLParser(page_html)
    courses: dict[str, dict[str, Any]] = {}
    for card in root.css(".course-theme"):
        link = card.css_first("h4 a[href]")
        href = link.attributes.get("href") if link is not None else None
        match = _COURSE.search(href or "")
        if link is None or not href or not match:
            continue
        course_id = match[1].lower()
        if course_id in courses:
            continue
        image = card.css_first(".course-theme-image img")
        src = image.attributes.get("src") if image is not None else None
        courses[course_id] = {
            "id": course_id,
            "url": urljoin(s.site_url, href),
            "title": clean_str(link.text()),
            "image": urljoin(s.site_url, src) if src else None,
        }
    return list(courses.values())


def parse_course(page_html: str, course: dict[str, Any], s: AscolotSettings, timezone: str) -> list[ScrapedEvent]:
    """The course's events: one per day of each group that is one meeting or sells a ticket per meeting."""
    root = LexborHTMLParser(page_html)
    heading = root.css_first(".course h1")
    title = clean_str(heading.text()) if heading is not None else course.get("title")
    if not title:
        return []
    crumbs = [clean_str(a.text()) for a in root.css(".breadcrumbs a")]
    genres = [c.lower() for c in crumbs[2:3] if c]
    body = root.css_first(".course-description")
    description = html_to_text(body.html) if body is not None and body.html else None
    lecturer = root.css_first("[id$=LecturerDisplayName]")
    artists = [name] if lecturer is not None and (name := clean_str(lecturer.text())) else []
    images = [course["image"]] if course.get("image") else []
    if not images:
        photo = root.css_first(".course-author-image img")
        src = photo.attributes.get("src") if photo is not None else None
        if src and not any(p in src for p in s.placeholder_images):
            images = [urljoin(s.site_url, src)]
    zone_info = zone(timezone)
    events: list[ScrapedEvent] = []
    for group in root.css(".course-info-timeslot"):
        group_id = (group.attributes.get("id") or "").removeprefix("GroupName")
        place = _text(group.css_first(".course-info-geo")).replace("/ מפה", "").strip(" /")
        if not group_id or not place or any(place.startswith(p) for p in s.skip_places):
            continue
        days = _days(group)
        price_titles = [_text(n) for n in group.css(".course-prices-title")]
        ticket = next((t for t in price_titles if t.startswith(s.ticket_label)), None)
        if ticket is None and len(days) != 1:
            continue  # a series sold whole
        amount = _AMOUNT.search(ticket or (price_titles[0] if price_titles else ""))
        price = Decimal(amount[1].replace(",", "")) if amount else None
        tickets = [TicketTier(name=s.ticket_name, price=price)] if price is not None else []
        hours = _HOURS.search(_text(group.css_first(".course-info-date")))
        register = group.css_first("a.pres-global-cours-btn[href]")
        seller = register.attributes.get("href") if register is not None else None
        venue = _venue(place, s)
        for day in days:
            start = datetime(day.year, day.month, day.day, int(hours[1]), int(hours[2])) if hours else None
            end = datetime(day.year, day.month, day.day, int(hours[3]), int(hours[4])) if hours and hours[3] else None
            begin = start or datetime(day.year, day.month, day.day)
            events.append(
                ScrapedEvent(
                    source=AscolotScraper.name,
                    source_event_id=f"{group_id}-{day.isoformat()}",
                    url=course["url"],
                    title=title,
                    description=description,
                    start_at=begin.replace(tzinfo=zone_info),
                    end_at=end.replace(tzinfo=zone_info) if end and end > begin else None,
                    time_known=start is not None,
                    venue_name=venue.venue_name,
                    address=venue.address,
                    city=venue.city or None,
                    latitude=venue.latitude,
                    longitude=venue.longitude,
                    source_genres=genres,
                    artists=artists,
                    organizer=s.organizer,
                    images=images,
                    tickets=tickets,
                    min_price=price,
                    max_price=price,
                    extra={"purchase_url": seller} if seller else {},
                )
            )
    return events


def _text(node: LexborNode | None) -> str:
    return re.sub(r"\s+", " ", node.text()).strip() if node is not None else ""


def _days(group: LexborNode) -> list[date]:
    """The meeting days of a group: each "<year>:" heading followed by its "27 נובמבר, 25 דצמבר" list."""
    card = group.css_first(".course-card-right")
    if card is None:
        return []
    days: list[date] = []
    year: int | None = None
    for node in card.css(".ccr-year, .ccr-dates"):
        text = _text(node)
        if "ccr-year" in (node.attributes.get("class") or ""):
            found = _YEAR.search(text)
            year = int(found[1]) if found else None
            continue
        if year is None:
            continue
        for d, month in _DAY.findall(text):
            if month in _MONTHS:
                try:
                    days.append(date(year, _MONTHS[month], int(d)))
                except ValueError:
                    continue
    return sorted(set(days))


def _venue(place: str, s: AscolotSettings) -> AscolotVenue:
    """The hall line ("מוזיאון ת"א לאמנות - אולם אסיא, ללא מקומות מסומנים") as its building's venue."""
    for note in s.seating_notes:
        place = place.replace(f", {note}", "").replace(note, "")
    place = place.strip(" ,")
    building = place.split(" - ")[0].strip()
    known = s.venues.get(building)
    if known is not None:
        return known
    return AscolotVenue(venue_name=building or place, city="")
