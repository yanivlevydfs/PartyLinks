"""Kaspit (kaspit-art.co.il) - an Israeli artist agency (Idan Raichel, Ehud Banai, Gidi Gov, Danny
Robas, Shazamat...): its "לוח הופעות" page lists every coming show of its artists.

WordPress, but the shows are a custom post type the REST API does not expose (``/wp-json/wp/v2/types``
has no ``events``) and each ``/events/<id>/`` page is empty. The listing page holds everything, one
``<li class="artist" ev-id="...">`` per show: the month name and day (no year), the artist, the weekday
and time, the venue as typed ("זאפה חיפה", "אסטרל בטרמינל - אילת"), an optional note (a festival, a
guest), the artist's 90x90 thumbnail (the full picture is the same file without ``-90x90``) and the
seller link ("הזמנה": Barby, Zappa, Eventer, Smarticket, Ticketmaster...). One request a run.

The year is the nearest one that is not past: the list runs in date order, so a month earlier than
the one before it is next year. No address or coordinates: the venue name is geocoded (with the town
it names, or the configured town of a known hall).
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, urlparse

from pydantic import Field
from selectolax.lexbor import LexborHTMLParser, LexborNode

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_TIME = re.compile(r"(\d{1,2}):(\d{2})")
_THUMB = re.compile(r"-\d+x\d+(?=\.\w+$)")

HEBREW_MONTHS = {
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


class KaspitSettings(SourceSettings):
    site_url: str = "https://kaspit-art.co.il"
    listing_path: str = "/%d7%94%d7%95%d7%a4%d7%a2%d7%95%d7%aa/"
    # Halls whose name names no town: name as typed (lower case, a part of it) -> town.
    venue_cities: dict[str, str] = Field(default_factory=dict)
    # Towns written after the hall ("זאפה חיפה", "הפטריה - קיבוץ דן").
    known_cities: list[str] = Field(default_factory=list)


class KaspitScraper(BaseScraper[KaspitSettings]):
    name = "kaspit"
    settings_model = KaspitSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        url = s.site_url + s.listing_path
        html = await self.get_text(url)
        today = datetime.now(zone(self.timezone)).date()
        for event in parse_listing(html, url, s, self.timezone, today):
            if is_current(event.start_at, event.end_at, RetentionSettings(), self.timezone):
                yield event


def _text(node: LexborNode | None) -> str | None:
    return clean_str(" ".join(node.text(separator=" ").split())) if node is not None else None


def city_of(venue: str | None, s: KaspitSettings) -> str | None:
    """The town a venue as typed names, or the configured town of a known hall."""
    if not venue:
        return None
    low = venue.lower()
    for name, city in s.venue_cities.items():
        if name.lower() in low:
            return city
    for city in sorted(s.known_cities, key=len, reverse=True):
        if re.search(rf"(?:^|[\s\-–,]){re.escape(city)}(?:$|[\s\-–,])", venue):
            return city
    return None


def _artist_page(item: LexborNode) -> str | None:
    """The artist's page, from the Facebook share link (``sharer.php?u=<page>``)."""
    share = item.css_first("a.facebook-share")
    href = share.attributes.get("href") if share is not None else None
    if not href:
        return None
    page = (parse_qs(urlparse(href).query).get("u") or [None])[0]
    return page if page and page.startswith("http") else None


def parse_listing(html: str, url: str, s: KaspitSettings, timezone: str, today: date) -> list[ScrapedEvent]:
    """Every show of the listing, its year the nearest one not past (the list is in date order)."""
    tz = zone(timezone)
    year = today.year
    previous: date | None = None
    out = []
    for item in LexborHTMLParser(html).css("li.artist[ev-id]"):
        event_id = item.attributes.get("ev-id")
        month = HEBREW_MONTHS.get(_text(item.css_first(".month-day")) or "")
        day_text = _text(item.css_first(".day"))
        if not event_id or not month or not day_text or not day_text.isdigit():
            continue
        try:
            day = date(year, month, int(day_text))
            if day < today - timedelta(days=1) or (previous and day < previous):
                year += 1
                day = date(year, month, int(day_text))
        except ValueError:
            continue
        previous = day
        info = item.css(".artist-info > p")
        artist = _text(item.css_first(".artist-info span"))
        head = _text(info[0]) if info else None
        clock = _TIME.search(head or "")
        start = datetime(day.year, day.month, day.day, tzinfo=tz)
        if clock:
            start += timedelta(hours=int(clock.group(1)), minutes=int(clock.group(2)))
        venue = _text(info[1]) if len(info) > 1 else None
        note = " ".join(t for t in (_text(p) for p in info[2:]) if t) or None
        if not artist:
            continue
        ticket = item.css_first("a.link-share")
        seller = clean_str(ticket.attributes.get("href")) if ticket is not None else None
        image = item.css_first(".date-img img")
        src = clean_str(image.attributes.get("src")) if image is not None else None
        page = _artist_page(item)
        out.append(
            ScrapedEvent(
                source=KaspitScraper.name,
                source_event_id=str(event_id),
                url=page or url,
                title=f"{artist} - {note}" if note else artist,
                start_at=start,
                time_known=clock is not None,
                venue_name=venue,
                city=city_of(venue, s),
                source_genres=["הופעות"],
                artists=[artist],
                images=[_THUMB.sub("", src)] if src and src.startswith("http") else [],
                organizer="כספית",
                extra={"purchase_url": seller or page or url},
            )
        )
    return out
