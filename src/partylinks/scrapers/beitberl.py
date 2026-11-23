"""Beit Berl Academic College (beitberl.ac.il, "המכללה האקדמית בית ברל"), by Kfar Saba - the college's public
events: study days, conferences, lectures, book launches, HaMidrasha's screenings and talks, culture days.

DRAFT - the listing and the sections are filled in after the recon (the site banned our VPN exit for a while).
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from html import unescape
from urllib.parse import urljoin, urlsplit

from pydantic import BaseModel, Field
from selectolax.lexbor import LexborHTMLParser, LexborNode

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, html_to_text, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_DAY = re.compile(r"(?<!\d)(\d{1,2})[./](\d{1,2})[./](\d{4}|\d{2})(?!\d)")
_CLOCK = re.compile(r"(?<![\d:])([01]?\d|2[0-3]):([0-5]\d)(?![\d:])")
_POST_ID = re.compile(r"\bpostid-(\d+)\b")  # the body's class: the post's id


class Place(BaseModel):
    venue_name: str
    address: str | None = None
    city: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class BeitBerlSettings(SourceSettings):
    site_url: str = "https://www.beitberl.ac.il"
    ajax_path: str = "/wp-admin/admin-ajax.php"
    events_path: str = "/events/"
    post_type: str = "event"
    per_page: int = Field(9, ge=1, le=100)
    max_pages: int = Field(60, ge=1)  # a guard against a listing that never ends, far above the site's size
    # The "מתי האירוע?" / "לאן להגיע" headings of an event page.
    when_heading: str = "מתי האירוע"
    where_heading: str = "לאן להגיע"
    # An online event's place line or title: not imported.
    online_words: list[str] = Field(
        default_factory=lambda: ["זום", "zoom", "מקוון", "מקוונ", "online", "וובינר", "אונליין"]
    )
    # A title of an event that is not public: open days and enrolment evenings, ceremonies of the college's own
    # students and staff, the student union's parties, camps.
    private_words: list[str] = Field(default_factory=list)
    # A place line -> the campus hall / gallery (first match wins); an unmatched campus line takes ``campus``.
    places: dict[str, Place] = Field(default_factory=dict)
    # Words of a place line on the campus (a building, a room, a hall).
    campus_words: list[str] = Field(default_factory=list)
    campus: Place
    city: str
    # A title / text word -> the categories declared beside it (our genres read them).
    labels: dict[str, list[str]] = Field(default_factory=dict)


@dataclass
class Page:
    """An event page as read: the fields every date of it shares."""

    post_id: str | None
    title: str
    days: list[date] = field(default_factory=list)
    start: time | None = None
    end: time | None = None
    place: str | None = None
    text: str | None = None
    image: str | None = None
    links: list[str] = field(default_factory=list)


class BeitBerlScraper(BaseScraper[BeitBerlSettings]):
    name = "beitberl"
    settings_model = BeitBerlSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:  # pragma: no cover - drafted, filled after the recon
        return
        yield


def _year(value: str) -> int:
    year = int(value)
    return year + 2000 if year < 100 else year


def _section(root: LexborNode, heading: str) -> LexborNode | None:
    """The block under the heading that names ``heading`` (its parent element)."""
    for node in root.css("h2, h3"):
        if heading in (node.text() or ""):
            return node.parent
    return None


def parse_page(html: str, base: str, s: BeitBerlSettings) -> Page | None:
    """An event page: title, the day(s) and hours under "מתי האירוע?", the place under "לאן להגיע", the text,
    the artwork and the text's links."""
    tree = LexborHTMLParser(html)
    h1 = tree.css_first("h1")
    title = clean_str(unescape(h1.text())) if h1 is not None else None
    body = tree.css_first("#SingleEvent")
    if not title or body is None:
        return None
    body_tag = tree.css_first("body")
    match = _POST_ID.search(body_tag.attributes.get("class") or "") if body_tag else None
    page = Page(post_id=match.group(1) if match else None, title=title)
    when = _section(body, s.when_heading)
    if when is not None:
        lines = [clean_str(li.text(separator=" ")) or "" for li in when.css("li")]
        for line in lines:
            for d, m, y in _DAY.findall(line):
                try:
                    page.days.append(date(_year(y), int(m), int(d)))
                except ValueError:
                    continue
        clocks = [time(int(h), int(mi)) for line in lines if not _DAY.search(line) for h, mi in _CLOCK.findall(line)]
        if clocks:
            page.start = clocks[0]
            page.end = clocks[1] if len(clocks) > 1 else None
    page.days = sorted(set(page.days))
    where = _section(body, s.where_heading)
    if where is not None:
        page.place = clean_str(" ".join(p.text(separator=" ") for p in where.css("p"))) or None
    entry = body.css_first(".entry")
    if entry is not None:
        page.text = html_to_text(entry.html)
        for link in entry.css("a[href]"):
            href = (link.attributes.get("href") or "").strip()
            if href.startswith(("http://", "https://")):
                page.links.append(href)
    img = body.css_first(".inR_con img") or body.css_first("picture img")
    if img is not None:
        src = img.attributes.get("data-src") or img.attributes.get("src") or ""
        if src.startswith(("http://", "https://", "/")) and not src.startswith("data:"):
            page.image = urljoin(base, src)
    return page


def resolve_place(line: str | None, s: BeitBerlSettings) -> Place | None:
    """The hall or gallery of a place line; a campus line no hall matches is the campus; None: elsewhere."""
    if not line:
        return s.campus
    folded = line.casefold()
    for word, place in s.places.items():
        if word.casefold() in folded:
            return place
    if any(w.casefold() in folded for w in s.campus_words):
        return s.campus
    return None


def declared(title: str, text: str | None, s: BeitBerlSettings) -> list[str]:
    """The categories a title (or, failing that, the text's first lines) names, by ``labels``."""
    out: list[str] = []
    for source in (title, (text or "")[:300]):
        folded = source.casefold()
        for word, labels in s.labels.items():
            if word.casefold() in folded:
                out += [word.casefold(), *(label.casefold() for label in labels)]
        if out:
            break
    return list(dict.fromkeys(out))


def is_public(title: str, place: str | None, s: BeitBerlSettings) -> bool:
    folded = f"{title} {place or ''}".casefold()
    if any(w.casefold() in folded for w in s.online_words):
        return False
    return not any(w.casefold() in title.casefold() for w in s.private_words)


def build_events(page: Page, url: str, s: BeitBerlSettings, timezone: str) -> list[ScrapedEvent]:
    """One event per day of the page (a range of days is one run: its first day to its last)."""
    if not page.days or not is_public(page.title, page.place, s):
        return []
    tz = zone(timezone)
    place = resolve_place(page.place, s)
    venue = place.venue_name if place else page.place
    address = (place.address if place else page.place) or None
    city = (place.city or s.city) if place else None
    lat = place.latitude if place else None
    lng = place.longitude if place else None
    genres = declared(page.title, page.text, s)
    own_host = urlsplit(s.site_url).hostname or ""
    purchase = next((u for u in page.links if (host := urlsplit(u).hostname) and not host.endswith(own_host)), None)
    extra = {"purchase_url": purchase} if purchase else {}
    if page.place:
        extra["room"] = page.place
    base_id = page.post_id or urlsplit(url).path.strip("/").rsplit("/", 1)[-1]
    events: list[ScrapedEvent] = []
    for day in page.days:
        start = datetime.combine(day, page.start or time.min, tzinfo=tz)
        end = datetime.combine(day, page.end, tzinfo=tz) if page.start and page.end else None
        if end and end <= start:
            end += timedelta(days=1)
        events.append(
            ScrapedEvent(
                source="beitberl",
                source_event_id=f"{base_id}-{day:%Y%m%d}" if len(page.days) > 1 else base_id,
                url=url,
                title=page.title,
                description=page.text,
                start_at=start,
                end_at=end,
                time_known=page.start is not None,
                venue_name=venue,
                address=address,
                city=city,
                latitude=lat,
                longitude=lng,
                source_genres=genres,
                images=[page.image] if page.image else [],
                image_url=page.image,
                organizer="המכללה האקדמית בית ברל",
                extra=dict(extra),
            )
        )
    return events


def current(events: list[ScrapedEvent], timezone: str) -> list[ScrapedEvent]:
    """The events that are not over (today's past hours too)."""
    retention = RetentionSettings()
    return [e for e in events if is_current(e.start_at, e.end_at, retention, timezone, time_known=e.time_known)]
