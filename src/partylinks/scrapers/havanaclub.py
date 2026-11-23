"""Havana Music Club (havanaclub.co.il, "הוואנה מיוזיק קלאב") - salsa, bachata and kizomba nights, Tel Aviv.

The site is Wix. Its nights are on sale on the ticket page (``listing_path``, "רכישת-כרטיסים"): a repeater
of the next ~6 nights (Tuesday, Thursday, Saturday), each item its dance styles ("סלסה | בצ׳אטה | אורבן
קיז"), weekday, date (``dd.mm.yyyy``) and its event page (``/event/<uuid>``, where the ticket is bought;
the page renders in the browser, so it gives nothing more). One request a run.

The items carry no time: each weekday's start (``start_times``, doors / the first lesson) is from the
nights' own pages ("מה הלו״ז": doors 21:00, lessons 21:15-21:30, the party from 22:30) and ends "in the
small hours", so no end. No price on the ticket page (the night pages say 60-70 ₪). No night has its own
artwork: each takes the ticket page's ``og:image``, the club's banner (its name over a Havana street photo).
The store's products (``/product-page/``) are courses and workshop packages: not read.

The club's one-off shows and holiday balls are on their own pages (``show_paths``: the live-shows board, the
New Year page), one more request each: a repeater item per show with its title, "יום שישי, 25 בדצמבר 2026"
(or "01 | רביעי | ספטמבר", no year: the next such day), "18:00 - 21:30" (doors; a range gives the end), its
artwork and its ``/event/<uuid>`` page (an item without one sells nothing: an old board item, skipped). The
weekly 60s / 80s / Saturday-noon dances are booked by phone and list no dates: not read.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import date, datetime
from typing import Any

from pydantic import Field
from selectolax.lexbor import LexborHTMLParser

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, og_image, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.livetickets import HEBREW_MONTHS

_DATE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")
_TIME = re.compile(r"^(\d{1,2}):(\d{2})$")
_EVENT = re.compile(r"/event/([0-9a-f-]{36})\b")
# A show's day: "25 בדצמבר 2026" or, split over the item, "01 רביעי ספטמבר" (weekday between, no year).
_WEEKDAY = r"(?:יום\s+)?(?:ראשון|שני|שלישי|רביעי|חמישי|שישי|שבת)"
_HE_DATE = re.compile(r"\b(\d{1,2})\s+(?:" + _WEEKDAY + r",?\s+)?ב?(" + "|".join(HEBREW_MONTHS) + r")(?:\s+(\d{4}))?")
_CLOCK = re.compile(r"\b(\d{1,2}):(\d{2})(?:\s*-\s*(\d{1,2}):(\d{2}))?")
_BARE = re.compile(r"\d+|" + _WEEKDAY + "|" + "|".join(HEBREW_MONTHS))  # an item text that is only a day part
_WIX_IMAGE = re.compile(r"https://static\.wixstatic\.com/media/[^/\s\"]+?~mv2\.\w+")
# Item texts that are labels, not the show's name.
_LABELS = frozenset({"דלתות", "הופעה", "מסיבה", "רכוש עכשיו", "לרכישה"})


class HavanaClubSettings(SourceSettings):
    site_url: str = "https://www.havanaclub.co.il"
    listing_path: str = "/רכישת-כרטיסים"
    venue_name: str
    address: str
    city: str
    latitude: float | None = None
    longitude: float | None = None
    # Weekday word as the item writes it ("יום חמישי" -> "חמישי") -> the night's start (doors / first lesson).
    start_times: dict[str, str] = Field(default_factory=dict)
    default_start: str = "21:00"
    title_format: str = "מסיבת {styles} בהוואנה קלאב"
    # Pages of one-off shows (a Wix repeater of dated items) -> the categories their shows get.
    show_paths: dict[str, list[str]] = Field(default_factory=dict)


class HavanaClubScraper(BaseScraper[HavanaClubSettings]):
    name = "havanaclub"
    settings_model = HavanaClubSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        page = await self.get_text(s.site_url + s.listing_path)
        nights, image = parse_listing(page), og_image(page)
        self.log.debug("listed %d nights", len(nights))
        events = []
        for night in nights:
            try:
                event = parse_night(night, s, self.timezone, image)
            except Exception as exc:  # noqa: BLE001 - one odd item never drops the others
                self.record_failure(f"night {night.get('id')}", exc)
                continue
            if event and is_current(event.start_at, event.end_at, RetentionSettings(), self.timezone):
                events.append(event)
        seen = {e.source_event_id for e in events}
        today = datetime.now(zone(self.timezone)).date()
        for path, genres in s.show_paths.items():
            show_page = await self.optional(f"show page {path}", self.get_text(s.site_url + path))
            for show in parse_shows(show_page or "", today):
                try:
                    event = parse_show(show, s, self.timezone, genres)
                except Exception as exc:  # noqa: BLE001 - one odd item never drops the others
                    self.record_failure(f"show {show.get('title')}", exc)
                    continue
                if (
                    event
                    and event.source_event_id not in seen
                    and is_current(event.start_at, event.end_at, RetentionSettings(), self.timezone)
                ):
                    seen.add(event.source_event_id)
                    events.append(event)
        for event in sorted(events, key=lambda e: e.start_at.timestamp() if e.start_at else 0.0):  # nearest first
            yield event


def parse_listing(page_html: str) -> list[dict[str, Any]]:
    """The ticket page -> one dict per repeater item: id (the event page's uuid), styles, weekday, date."""
    nights = []
    for item in LexborHTMLParser(page_html).css("[role=listitem]"):
        link = next((a for a in item.css("a[href]") if _EVENT.search(a.attributes.get("href") or "")), None)
        if link is None:
            continue
        texts = [t for n in item.css("[data-testid=richTextElement]") if (t := clean_str(n.text()))]
        date = next((t for t in texts if _DATE.search(t)), None)
        weekday = next((t for t in texts if t.startswith("יום ")), None)
        styles = next((t for t in texts if t not in (date, weekday)), None)
        match = _EVENT.search(link.attributes.get("href") or "")
        nights.append({
            "id": match.group(1) if match else "",
            "styles": styles or "",
            "weekday": weekday or "",
            "date": date or "",
        })  # fmt: skip
    return nights


def parse_night(
    night: dict[str, Any], s: HavanaClubSettings, timezone: str, image: str | None = None
) -> ScrapedEvent | None:
    """One repeater item -> its night; None without a date, an event page or a style. ``image``: the club's banner."""
    day = _DATE.search(night["date"])
    if not day or not night["id"] or not night["styles"]:
        return None
    weekday = night["weekday"].removeprefix("יום ").strip()
    clock = _TIME.match(s.start_times.get(weekday, s.default_start))
    hour, minute = (int(clock[1]), int(clock[2])) if clock else (21, 0)
    start_at = datetime(int(day[3]), int(day[2]), int(day[1]), hour, minute, tzinfo=zone(timezone))
    styles = [p for part in night["styles"].split("|") if (p := part.strip())]
    return ScrapedEvent(
        source=HavanaClubScraper.name,
        source_event_id=night["id"],
        url=f"{s.site_url}/event/{night['id']}",
        title=s.title_format.format(styles=" | ".join(styles)),
        start_at=start_at,
        venue_name=s.venue_name,
        address=s.address,
        city=s.city,
        latitude=s.latitude,
        longitude=s.longitude,
        source_genres=[p.lower() for p in styles],
        images=[image] if image and image.startswith("http") else [],
    )


def parse_shows(page_html: str, today: date) -> list[dict[str, Any]]:
    """A show page -> one dict per dated repeater item: id, title, day, start / end clock, artwork."""
    shows = []
    for item in LexborHTMLParser(page_html).css("[role=listitem]"):
        texts = [t for n in item.css("[data-testid=richTextElement]") if (t := clean_str(n.text()))]
        joined = " ".join(texts)
        day, clock = _HE_DATE.search(joined), _CLOCK.search(joined)
        title = next(
            (t for t in texts if t not in _LABELS and not _HE_DATE.search(t) and not _CLOCK.fullmatch(t)
             and not _BARE.fullmatch(t)),
            None,
        )  # fmt: skip
        link = next((m for a in item.css("a[href]") if (m := _EVENT.search(a.attributes.get("href") or ""))), None)
        if not day or not clock or not title or not link:  # no event page: nothing on sale (an old board item)
            continue
        month, dom = HEBREW_MONTHS[day[2]], int(day[1])
        year = int(day[3]) if day[3] else today.year + ((month, dom) < (today.month, today.day))
        image = next((m[0] for i in item.css("img") if (m := _WIX_IMAGE.search(i.attributes.get("src") or ""))), None)
        shows.append({
            "id": link.group(1),
            "title": title,
            "day": date(year, month, dom),
            "start": (int(clock[1]), int(clock[2])),
            "end": (int(clock[3]), int(clock[4])) if clock[3] else None,
            "image": image,
        })  # fmt: skip
    return shows


def parse_show(show: dict[str, Any], s: HavanaClubSettings, timezone: str, genres: list[str]) -> ScrapedEvent:
    """One show item -> its event (its ``/event/<uuid>`` page, where the ticket is bought)."""
    tz, day = zone(timezone), show["day"]
    start_at = datetime(day.year, day.month, day.day, *show["start"], tzinfo=tz)
    end_at = datetime(day.year, day.month, day.day, *show["end"], tzinfo=tz) if show["end"] else None
    if end_at and end_at <= start_at:  # past midnight
        end_at = None
    return ScrapedEvent(
        source=HavanaClubScraper.name,
        source_event_id=show["id"],
        url=f"{s.site_url}/event/{show['id']}",
        title=show["title"],
        start_at=start_at,
        end_at=end_at,
        venue_name=s.venue_name,
        address=s.address,
        city=s.city,
        latitude=s.latitude,
        longitude=s.longitude,
        source_genres=[g.lower() for g in genres],
        images=[show["image"]] if show["image"] else [],
    )
