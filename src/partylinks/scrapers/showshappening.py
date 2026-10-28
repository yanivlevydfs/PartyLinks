"""ShowsHappening (showshappening.com) - ticketing in Malta, the UK, Germany and Spain: clubbing,
classical concerts, live music, theatre, comedy, kids' shows, excursions and museums.

Listing: each country's home page (``/?country=mt|gb|de|es``) is a calendar of every upcoming show:
one link per date with the event's path, the date, its categories and tags, the flyer and title.
Details: the event page's own HTML (``og:description``, ``og:image``, the hidden ``showdate`` with
the next show's day and time, ``eventid``). One event per listed date; a recurring event's other
dates take the next show's time of day. Venue, address and prices are loaded by the page's
JavaScript from a private API and are not read, so these events carry their country only.
robots.txt allows everything.
"""

from __future__ import annotations

import html as html_lib
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import Field

from partylinks.models import ScrapedEvent
from partylinks.parsing import ParseError, clean_str
from partylinks.scrapers.abroad import local_zone
from partylinks.scrapers.base import BaseScraper, SourceSettings

_CARD = re.compile(
    r'<a href="(/[^"]+)" class="calendar_events_result_event_container" data-show-date="([^"]*)"\s*'
    r'data-category="([^"]*)" data-tags="([^"]*)">\s*<img src="([^"]*)"[^>]*>\s*<p>(.*?)</p>', re.S)
_META = re.compile(r'<meta property="(og:[a-z]+|description)" content="([^"]*)"')
_SHOWDATE = re.compile(r'class="showdate" value="[A-Za-z]+ (\d{1,2}-[A-Za-z]{3}-\d{4}) (\d{1,2}:\d{2})"')
_EVENTID = re.compile(r'id="eventid" value="([0-9a-f-]+)"')
_CITY = re.compile(r'<input type="hidden" name="city" value="([^"]+)"')

# ShowsHappening's categories and tags as the category words the enrichment knows. A tag that only
# describes the mood ("Romantic", "Night", "Indoor") adds nothing.
_CATEGORIES = {
    "clubbing and parties": "nightclubs", "classical music": "classical", "live music": "concert",
    "arts and culture": "culture", "kids": "kids", "comedy": "comedy", "educational": "lecture",
    "sports": "sports", "health and fitness": "wellness", "excursions and experiences": "tours",
}
_TAGS = {
    "theatre": "theatre", "pantomine": "theatre", "musical": "musicals", "opera": "opera", "choir": "classical",
    "orchestra": "classical", "christmas": "holidays", "halloween": "halloween", "jazz": "jazz", "rock": "rock",
    "techno": "techno", "house": "house", "afro house": "afro house", "hip hop": "hip hop", "electronic": "electronic",
    "disco": "disco", "festival": "festival", "dance show": "dance", "food & drink": "food", "stand up": "comedy",
    "comedy night": "comedy", "visual arts": "exhibitions", "museum": "exhibitions", "beach club": "nightclubs",
    "clubbing": "nightclubs", "children": "kids", "family-friendly": "family", "tribute": "concert",
}


class ShowsHappeningSettings(SourceSettings):
    site_url: str
    countries: list[str] = Field(default_factory=lambda: ["mt", "gb", "de", "es"])


class ShowsHappeningScraper(BaseScraper[ShowsHappeningSettings]):
    name = "showshappening"
    settings_model = ShowsHappeningSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        listed: dict[str, dict[str, Any]] = {}
        for country in self.settings.countries:
            page = await self.get_text(self.settings.site_url + "/", params={"country": country})
            for card in cards(page, country):
                entry = listed.setdefault(card["path"], {**card, "dates": []})
                entry["dates"].append(card["date"])
        self.log.info("listed %d events (%d shows)", len(listed), sum(len(e["dates"]) for e in listed.values()))
        async for events in self.stream_isolated(list(listed.values()), self._fetch_event, label=lambda e: e["path"]):
            for event in events:
                yield event

    async def _fetch_event(self, listed: dict[str, Any]) -> list[ScrapedEvent]:
        page = await self.get_text(self.settings.site_url + listed["path"])
        return parse_event(listed, page, self.settings.site_url, self.timezone)


def cards(page_html: str, country: str) -> list[dict[str, Any]]:
    """The calendar's show links: path, date, categories, tags, flyer, title and country."""
    out = []
    for path, day, categories, tags, image, title in _CARD.findall(page_html):
        try:
            date = datetime.strptime(day, "%Y-%B-%d").date()
        except ValueError:
            continue
        out.append({
            "path": "/" + html_lib.unescape(path).lstrip("/"),  # UK links come as "//e/gb/..."
            "date": date, "country": country.upper(),
            "categories": [c.strip() for c in html_lib.unescape(categories).split(",") if c.strip()],
            "tags": [t.strip() for t in html_lib.unescape(tags).split(",") if t.strip()],
            "image": html_lib.unescape(image), "title": clean_str(html_lib.unescape(title)),
        })
    return out


def categories(listed: dict[str, Any]) -> list[str]:
    """Category words for the enrichment from the event's categories and tags."""
    words = [_CATEGORIES.get(c.casefold()) for c in listed["categories"]] + [_TAGS.get(t.casefold()) for t in listed["tags"]]
    return list(dict.fromkeys(w for w in words if w))


def parse_event(listed: dict[str, Any], page_html: str, site_url: str, default_tz: str) -> list[ScrapedEvent]:
    """One event per listed date that is not over yet."""
    meta = {k: html_lib.unescape(v) for k, v in _META.findall(page_html)}
    # UK pages are drawn by JavaScript: their og:title is the site's ("ShowsHappening | Tickets in Malta").
    title = listed["title"] or clean_str((meta.get("og:title") or "").removesuffix("| ShowsHappening"))
    city = clean_str(html_lib.unescape(m.group(1))) if (m := _CITY.search(page_html)) else None
    event_id = (m.group(1) if (m := _EVENTID.search(page_html)) else None) or listed["path"].strip("/").replace("/", "-")
    if not title:
        raise ParseError(f"{listed['path']}: event without title")
    country = listed["country"]
    zone = ZoneInfo(local_zone("GB" if country == "UK" else country, default_tz))
    show = _SHOWDATE.search(page_html)
    clock: time | None = datetime.strptime(show.group(2), "%H:%M").time() if show else None
    image = clean_str(meta.get("og:image")) or clean_str(listed["image"])
    now = datetime.now(UTC)
    events = []
    # ponytail: a recurring event's other dates take the next show's hour; per-date hours need the API.
    for day in sorted(set(listed["dates"])):
        start = datetime.combine(day, clock or time(), zone)
        if start.date() < now.astimezone(zone).date():  # never past events
            continue
        events.append(ScrapedEvent(
            source=ShowsHappeningScraper.name,
            source_event_id=f"{event_id}-{day.isoformat()}",
            url=site_url + listed["path"],
            title=title,
            description=clean_str(meta.get("og:description") or meta.get("description")),
            city=city,
            start_at=start,
            time_known=clock is not None,
            country=country,
            source_genres=categories(listed),
            image_url=image,
            images=[image] if image else [],
            currency="GBP" if country == "GB" else "EUR",
        ))
    return events
