"""Habama (habama.co.il) - performing-arts listings portal: theatre, dance, classical, kids, comedy.

An old ASP.NET site with no API, sitemap or structured data. Its "all events of the day"
page (``EventAllforDay.aspx?d=&m=&y=``) lists every performance of one day nationwide (the
``Subj`` / ``Area`` parameters do not filter it): one table row per performance with the hall,
a short note (director, genre, ages), the show's link and title, and the time; the category
("תיאטרון", "מחול", "ילדים"...) heads each group of rows. One GET per day over ``days`` (the
site lists about two months ahead). Pages are slow (5-90 s) and the backend answers 502 now
and then, so the timeout is long and 502s are retried by the transport. ``url`` is the habama
show page. One more GET per show (not per performance) reads its picture: the page's script holds
its media as ``var resultSet = new String("artist]id]1]Upload/MediaFiles]small.jpg]big.jpg]title|...")``
(type 1 = image). Habama shows no price: the show page's "מחיר" column is empty (40 of 40 shows
sampled, Nov 2026), so prices stay unknown.
robots.txt only disallows SEO bots.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import date, datetime, time, timedelta
from urllib.parse import quote

from selectolax.lexbor import LexborHTMLParser

from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, zone
from partylinks.scrapers.base import BaseScraper, SourceSettings

_EVENT_ID = re.compile(r"EventId=(\d+)", re.IGNORECASE)
_TIME = re.compile(r"^(\d{1,2}):(\d{2})$")
_MEDIA = re.compile(r'var resultSet = new String\("([^"]*)"\)')


class HabamaSettings(SourceSettings):
    site_url: str
    day_path: str  # the day page; d/m/y are added
    days: int = 45  # today plus this many days ahead


class HabamaScraper(BaseScraper[HabamaSettings]):
    name = "habama"
    settings_model = HabamaSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        today = datetime.now(zone(self.timezone)).date()
        days = [today + timedelta(days=n) for n in range(s.days + 1)]

        async def day_events(day: date) -> list[ScrapedEvent]:
            url = f"{s.site_url}{s.day_path}?Subj=1&Area=1&d={day.day}&m={day.month}&y={day.year % 100}"
            return parse_day(await self.get_text(url), day, self.timezone)

        shows: dict[str, list[ScrapedEvent]] = {}  # a show's performances share its picture
        async for events in self.stream_isolated(days, day_events, lambda d: f"day {d.isoformat()}"):
            for event in events:
                shows.setdefault(event.extra["show_id"], []).append(event)

        async def with_images(show: str) -> list[ScrapedEvent]:
            performances = shows[show]
            page = await self.optional(f"show {show}", self.get_text(str(performances[0].url)))
            for event in performances:
                event.images = show_images(page or "", s.site_url)
                event.image_url = event.images[0] if event.images else None
            return performances

        # ponytail: performances wait for the whole day crawl (memory ~ a few thousand events).
        async for events in self.stream_isolated(list(shows), with_images, lambda show: f"show {show}"):
            for event in events:
                yield event


def show_images(page_html: str, site_url: str) -> list[str]:
    """The show page's pictures, big version first (its script's ``resultSet``)."""
    media = _MEDIA.search(page_html)
    images = []
    for item in (media.group(1) if media else "").split("|"):
        parts = item.split("]")
        if len(parts) > 5 and parts[2] == "1" and (name := parts[5] or parts[4]):
            images.append(f"{site_url}/{parts[3].strip('/')}/{quote(name)}")
    return list(dict.fromkeys(images))


def _split_hall(hall: str | None) -> tuple[str | None, str | None]:
    """ "אודיטוריום אריסון, תל אביב" -> (venue, city); no comma -> (hall, None): enrichment then
    finds the city in the hall's name ("היכל התרבות ראשון לציון")."""
    venue, comma, city = (hall or "").rpartition(",")
    return (clean_str(venue), clean_str(city)) if comma else (hall, None)


def parse_day(page_html: str, day: date, timezone: str) -> list[ScrapedEvent]:
    """Every performance of one day page; a show listed under two categories is one event with both."""
    events: dict[str, ScrapedEvent] = {}
    category: str | None = None
    # ASP.NET renders rows for an unknown User-Agent with less inline style: only the padding is in both.
    for row in LexborHTMLParser(page_html).css('tr[style*="padding-right:4px"]'):
        # The category cell (rowspan) sits on the first row of its group only.
        if (cell := row.css_first("td[rowspan][class]")) is not None:
            category = clean_str(cell.text())
        # Hall and show each have a "<" arrow link before the named one: the named ones sit in rtl cells.
        links = {a.attributes.get("href") or "": a for a in row.css('td[dir="rtl"] a')}
        show = next((a for href, a in links.items() if "Event.aspx" in href), None)
        if show is None:
            continue
        href = show.attributes.get("href") or ""
        event_id = _EVENT_ID.search(href)
        title = clean_str(show.text())
        if not event_id or not title:
            continue
        hall = next((clean_str(a.text()) for href, a in links.items() if "HallGeneral" in href), None)
        note = row.css_first("td[title]")
        clock = _TIME.match(clean_str(t.text()) or "") if (t := row.css_first("span.EventsMidTextN1")) else None
        start = time(int(clock[1]), int(clock[2])) if clock else time()  # date only
        # A show plays many times: one event per performance.
        key = f"{event_id[1]}-{day:%Y%m%d}{start:%H%M}"
        if key in events:
            if category and category not in events[key].source_genres:
                events[key].source_genres.append(category)
            continue
        venue, city = _split_hall(hall)
        events[key] = ScrapedEvent(
            source=HabamaScraper.name,
            source_event_id=key,
            url=href.replace("http://", "https://", 1),  # needs its Subj/Area, else a 500
            title=title,
            description=clean_str(note.text()) if note else None,
            start_at=datetime.combine(day, start, tzinfo=zone(timezone)),
            time_known=clock is not None,
            venue_name=venue,
            city=city,
            source_genres=[category] if category else [],
            extra={"show_id": event_id[1]},
        )
    return list(events.values())
