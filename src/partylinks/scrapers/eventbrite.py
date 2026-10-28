"""Eventbrite (eventbrite.com) - conferences, meetups, workshops, parties and community events.

Listing: the Israel discovery pages ``/d/israel/all-events/?page=N`` (~75 events, 20 a page). Each
page carries its results in ``window.__SERVER_DATA__``: name, local start / end with the venue's
zone, venue with address and coordinates, categories (``tags``), artwork, organizer and the full
description. Details: the event page's schema.org ``AggregateOffer`` gives the price range,
currency and whether it is sold out. Online events are skipped. robots.txt allows these pages.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from pydantic import Field

from partylinks.models import ScrapedEvent
from partylinks.parsing import ParseError, clean_str, jsonld_nodes, parse_iso, schema_types, to_decimal, to_float
from partylinks.scrapers.base import BaseScraper, SourceSettings

_MARK = "window.__SERVER_DATA__ = "
# Eventbrite's categories and formats as the category words the enrichment knows (its own names,
# "Business & Professional", match nothing). Unlisted ones (Other, Gala, Home & Lifestyle...) add none;
# music genres still come from the title / description.
_CATEGORIES = {
    "EventbriteCategory/101": "meetups",        # Business & Professional -> meetups
    "EventbriteCategory/102": "technology",     # Science & Technology -> hi-tech
    "EventbriteCategory/103": "concert",        # Music -> live shows
    "EventbriteCategory/104": "screening",      # Film, Media & Entertainment -> cinema
    "EventbriteCategory/105": "culture",        # Performing & Visual Arts
    "EventbriteCategory/107": "wellness",       # Health & Wellness -> yoga & wellness
    "EventbriteCategory/108": "sports",         # Sports & Fitness
    "EventbriteCategory/109": "tours",          # Travel & Outdoor
    "EventbriteCategory/110": "food",           # Food & Drink
    "EventbriteCategory/111": "volunteering",   # Charity & Causes
    "EventbriteCategory/113": "culture",        # Community & Culture
    "EventbriteCategory/114": "religious",      # Religion & Spirituality
    "EventbriteCategory/115": "family",         # Family & Education -> kids
    "EventbriteCategory/116": "holidays",       # Seasonal & Holiday
    "EventbriteFormat/1": "conference",         # Conference
    "EventbriteFormat/2": "lecture",            # Seminar or Talk
    "EventbriteFormat/3": "expo",               # Tradeshow, Consumer Show, or Expo -> conference
    "EventbriteFormat/4": "conference",         # Convention
    "EventbriteFormat/5": "festival",           # Festival or Fair
    "EventbriteFormat/6": "concert",            # Concert or Performance
    "EventbriteFormat/7": "screening",          # Screening
    "EventbriteFormat/9": "workshop",           # Class, Training, or Workshop
    "EventbriteFormat/10": "meetups",           # Meeting or Networking Event
    "EventbriteFormat/11": "nightclubs",        # Party or Social Gathering
    "EventbriteFormat/13": "sports",            # Tournament
    "EventbriteFormat/15": "sports",            # Race or Endurance Event
    "EventbriteFormat/16": "tours",             # Tour
    "EventbriteFormat/17": "attractions",       # Attraction
}
# Events listed with no category at all: category words from the title (Hebrew or English).
_TITLE_WORDS = [
    (re.compile(r"workshop|סדנ", re.I), "workshop"),
    (re.compile(r"\btours?\b|סיור|experience|מקרוב", re.I), "tours"),
    (re.compile(r"lecture|\btalk\b|הרצא", re.I), "lecture"),
    (re.compile(r"demo day|accelerator|startup|סטארט", re.I), "startups"),
    (re.compile(r"conference|summit|כנס", re.I), "conference"),
]


class EventbriteSettings(SourceSettings):
    site_url: str
    listing_path: str  # /d/israel/all-events/
    max_pages: int = Field(30, ge=1)


class EventbriteScraper(BaseScraper[EventbriteSettings]):
    name = "eventbrite"
    settings_model = EventbriteSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        listed = await self._list()
        self.log.info("listed %d upcoming events", len(listed))
        async for event in self.stream_isolated(listed, self._fetch_event, label=lambda e: e.get("url", "")):
            yield event

    async def _list(self) -> list[dict[str, Any]]:
        s, events, now = self.settings, {}, datetime.now(UTC)
        pages = 1
        page = 1
        while page <= min(pages, s.max_pages):
            data = server_data(await self.get_text(s.site_url + s.listing_path, params={"page": page}))
            found = ((data.get("search_data") or {}).get("events") or {})
            pages = int((found.get("pagination") or {}).get("page_count") or 1)
            for e in found.get("results") or []:
                end = _when(e, "end") or _when(e, "start")
                if e.get("id") and e.get("url") and not e.get("is_online_event") and not e.get("is_cancelled") \
                        and (end is None or end > now):  # never past events
                    events[e["id"]] = e
            page += 1
        if pages > s.max_pages:
            self.log.warning("listing truncated at max_pages=%d (%d pages)", s.max_pages, pages)
        return list(events.values())

    async def _fetch_event(self, listed: dict[str, Any]) -> ScrapedEvent | None:
        page = await self.optional(listed["url"], self.get_text(listed["url"]))
        return parse_event(listed, offer(page or ""))


def server_data(page_html: str) -> dict[str, Any]:
    """The page's ``window.__SERVER_DATA__`` object ({} when missing)."""
    i = page_html.find(_MARK)
    if i < 0:
        return {}
    try:
        data, _ = json.JSONDecoder().raw_decode(page_html, i + len(_MARK))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def offer(page_html: str) -> dict[str, Any]:
    """The event page's schema.org (Aggregate)Offer ({} when missing)."""
    return next((n for n in jsonld_nodes(page_html) if schema_types(n) & {"aggregateoffer", "offer"}), {})


def _when(e: dict[str, Any], which: str) -> datetime | None:
    day, clock = e.get(f"{which}_date"), e.get(f"{which}_time")
    return parse_iso(f"{day}T{clock or '00:00'}", e.get("timezone")) if day else None


def parse_event(e: dict[str, Any], deal: dict[str, Any]) -> ScrapedEvent | None:
    """One event from its listing entry and its page's offer."""
    title, start = clean_str(e.get("name")), _when(e, "start")
    if not title or not start:
        raise ParseError(f"{e.get('id')}: event without name or start")
    venue = e.get("primary_venue") or {}
    place = venue.get("address") or {}
    country = clean_str(place.get("country")) or "IL"
    image = ((e.get("image") or {}).get("original") or {}).get("url")
    tags = list(dict.fromkeys(w for t in e.get("tags") or [] if (w := _CATEGORIES.get(t.get("tag", "")))))
    tags = tags or [word for pattern, word in _TITLE_WORDS if pattern.search(title)]
    low, high = to_decimal(deal.get("lowPrice") or deal.get("price")), to_decimal(deal.get("highPrice"))
    return ScrapedEvent(
        source=EventbriteScraper.name,
        source_event_id=str(e["id"]),
        url=e["url"],
        title=title,
        description=clean_str(e.get("full_description") or e.get("summary")),
        start_at=start,
        end_at=_when(e, "end"),
        time_known=bool(e.get("start_time")),
        venue_name=clean_str(venue.get("name")),
        address=clean_str(place.get("localized_address_display") or place.get("address_1")),
        city=clean_str(place.get("city")),
        latitude=to_float(place.get("latitude")),
        longitude=to_float(place.get("longitude")),
        country=None if country == "IL" else country,
        source_genres=tags,
        image_url=clean_str(image),
        images=[image] if image else [],
        currency=clean_str(deal.get("priceCurrency")) or "ILS",
        min_price=low,
        max_price=high if high is not None and high != low else None,
        is_sold_out="soldout" in str(deal.get("availability", "")).lower(),
    )
