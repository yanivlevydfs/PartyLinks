"""HaMenagen (hamenagen.net) - Jewish / Hasidic music site's concert calendar.

The events page (``/events/``) is filled from the site's own app API,
``/wp-json/hm-app/v1/events`` (GET, no paging: every upcoming event in one call, ``total``
alongside). Each item gives a title, date and "HH:MM" time, one free-text location
("<venue>, <city>" or just a venue), the seller's link, a flyer and the performing artists
(name, photo, singer page). There is no event page of its own, so the seller's link is both
the URL and ``extra["purchase_url"]`` (dedup ties it to that seller's listing: mostly Tickchak
and its Glatticket white-label). robots.txt allows everything.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, datetime, time
from typing import Any

from partylinks.artists import ArtistProfile
from partylinks.models import ScrapedEvent
from partylinks.parsing import ParseError, clean_str, read_clock, zone
from partylinks.scrapers.base import BaseScraper, SourceSettings


class HamenagenSettings(SourceSettings):
    site_url: str
    api_path: str  # the app API's events list
    events_path: str  # the events page (link of events without a seller's link)


class HamenagenScraper(BaseScraper[HamenagenSettings]):
    name = "hamenagen"
    settings_model = HamenagenSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        payload = await self.get_json(s.site_url + s.api_path, expect=dict)
        items = payload.get("items")
        if not isinstance(items, list):
            raise ParseError(f"{self.name}: no items list in the events API")
        for item in items:
            try:
                event = parse_item(item, s.site_url + s.events_path, self.timezone)
            except Exception as exc:  # noqa: BLE001 - one odd item never drops the others
                self.record_failure(f"event {item.get('id') if isinstance(item, dict) else item!r}", exc)
                continue
            if event is not None:
                yield event


def _split_location(location: str | None) -> tuple[str | None, str | None]:
    """Split "בנייני האומה- אולם אוסשקין, ירושלים" -> (venue, city); no comma -> (venue, None):
    enrichment then finds a city in the venue name ("FRIENDS נהריה")."""
    venue, comma, city = (location or "").rpartition(",")
    return (clean_str(venue), clean_str(city)) if comma else (clean_str(location), None)


def parse_item(item: dict[str, Any], events_url: str, timezone: str) -> ScrapedEvent | None:
    """One API item -> event; None without an id, title or date."""
    event_id, title = clean_str(item.get("id")), clean_str(item.get("title"))
    try:
        day = date.fromisoformat(str(item.get("date")))
    except ValueError:
        return None
    if not event_id or not title:
        return None
    clock = read_clock(item.get("timeLabel"))
    start_time, known = clock or time(), clock is not None  # no time in the label: date only
    venue, city = _split_location(clean_str(item.get("location")))
    ticket_url = clean_str(item.get("ticketUrl"))
    artists = [a for a in item.get("artists") or [] if isinstance(a, dict) and clean_str(a.get("name"))]
    names = [clean_str(a["name"]) for a in artists] or [
        n for n in (clean_str(p) for p in str(item.get("singerName") or "").split(",")) if n
    ]
    return ScrapedEvent(
        source=HamenagenScraper.name,
        source_event_id=event_id,
        url=ticket_url or events_url,
        title=title,
        source_genres=["concert", "מוזיקה יהודית"],  # a Jewish / Hasidic music concert calendar
        start_at=datetime.combine(day, start_time, tzinfo=zone(timezone)),
        time_known=known,
        venue_name=venue,
        city=city,
        artists=names,
        artist_profiles=[
            ArtistProfile(
                name=clean_str(a["name"]),
                source=HamenagenScraper.name,
                kind="singer",
                image_url=clean_str(a.get("image")),
                profile_url=clean_str(a.get("url")),
            )
            for a in artists
        ],
        image_url=clean_str(item.get("flyerFull")) or clean_str(item.get("flyer")),
        extra={"purchase_url": ticket_url} if ticket_url else {},
    )
