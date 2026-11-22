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
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from pydantic import Field
from selectolax.lexbor import LexborHTMLParser

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, og_image, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_DATE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")
_TIME = re.compile(r"^(\d{1,2}):(\d{2})$")
_EVENT = re.compile(r"/event/([0-9a-f-]{36})\b")


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
