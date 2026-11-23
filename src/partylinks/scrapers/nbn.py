"""Nefesh B'Nefesh (nbn.org.il) - the community calendar for English-speaking olim: socials, young professionals'
mixers and networking, singles evenings, language exchange, lectures, tours, sport and holiday events across Israel.

WordPress with Modern Events Calendar (MEC). Listing: MEC's iCal feed (``/?mec-ical-feed=1``, published for
calendar subscriptions) lists every event in one request (~2.5 MB, the whole archive; PHP warnings before
``BEGIN:VCALENDAR`` are cut off): UID, start / end in Asia/Jerusalem, title, description, page URL, categories,
the place as one line ("30 Yom Tov Street, Tel Aviv"), the artwork (``ATTACH``) and, for repeating events, an
``RRULE`` / ``RDATE``. Repeating events give one event per occurrence over ``recurrence_days``.
Online events (place "Zoom" / "Virtual" / "Online", or no place and an online category) are not imported.
MEC writes most ends a day late ("19:45" to the next day's "20:45"): a span of one to two days whose end
clock is after the start clock ends on the start day.
Details: each upcoming event's page (one request per event, not per occurrence) gives a schema.org Event with
the venue's name and address apart, the organizer and the cost (``mec-events-event-cost``: "NIS165.00", only
when the event states one; the JSON-LD's price "0" means none was given), and the "Register Here" link to the
organizer's page (``mec-booking-button``), kept as ``extra["purchase_url"]``. A failed page keeps the feed's data.
No coordinates: the address is geocoded; a place line that names no town takes the town of the event's area
category ("Tel Aviv", "Jerusalem"...). robots.txt disallows only old news paths.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from decimal import Decimal
from html import unescape
from typing import Any

import icalendar
from dateutil.rrule import rruleset, rrulestr
from pydantic import Field
from selectolax.lexbor import LexborHTMLParser

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import clean_str, html_to_text, jsonld_nodes, schema_types, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_AMOUNT = re.compile(r"(\d+(?:[.,]\d+)?)")


class NbnSettings(SourceSettings):
    site_url: str = "https://www.nbn.org.il"
    ical_path: str = "/?mec-ical-feed=1"
    # A repeating event: its occurrences this many days ahead.
    recurrence_days: int = Field(60, ge=1)
    # A place line naming one of these is an online event (lower case).
    online_words: list[str] = Field(default_factory=lambda: ["zoom", "virtual", "online", "webinar", "livestream"])
    # Categories that make an event with no place online.
    online_categories: list[str] = Field(default_factory=lambda: ["Virtual", "Zoom"])
    # A site category -> the words declared for it (our genre keywords); a category not named is declared as it is.
    labels: dict[str, list[str]] = Field(default_factory=dict)
    # Declared for every event (the calendar is for English speakers).
    declared: list[str] = Field(default_factory=lambda: ["english"])
    # Area categories -> the town, for a place line that names none.
    city_categories: dict[str, str] = Field(default_factory=dict)


class NbnScraper(BaseScraper[NbnSettings]):
    name = "nbn"
    settings_model = NbnSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        feed = await self.get_text(s.site_url + s.ical_path)
        now = datetime.now(zone(self.timezone))
        by_url: dict[str, list[ScrapedEvent]] = {}
        for item in feed_events(feed):
            try:
                events = parse_vevent(item, s, self.timezone, now)
            except Exception as exc:  # noqa: BLE001 - one odd entry never drops the others
                self.record_failure(f"event {item.get('UID')}", exc)
                continue
            for event in events:
                if is_current(event.start_at, event.end_at, RetentionSettings(), self.timezone):
                    by_url.setdefault(str(event.url), []).append(event)
        self.log.debug("listed %d upcoming events", len(by_url))
        # Nearest first: the pages in the order of their first occurrence.
        urls = sorted(by_url, key=lambda u: min(e.start_at for e in by_url[u] if e.start_at))

        async def detail(url: str) -> list[ScrapedEvent]:
            page = await self.optional(f"page {url}", self.get_text(url))
            events = by_url[url]
            return [with_page(e, page, s) for e in events] if page else events

        async for events in self.stream_isolated(urls, detail, label=lambda u: u):
            for event in sorted(events, key=lambda e: e.start_at or now):
                if event.address or event.venue_name:  # no place at all ("a comedy tour"): nothing to map
                    yield event


def feed_events(feed: str) -> list[Any]:
    """The VEVENTs of the feed; whatever the server printed before ``BEGIN:VCALENDAR`` is dropped."""
    start = feed.find("BEGIN:VCALENDAR")
    if start < 0:
        return []
    calendar = icalendar.Calendar.from_ical(feed[start:])
    return list(calendar.walk("VEVENT"))


def _local(value: Any, timezone: str) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=zone(timezone))
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time(), tzinfo=zone(timezone))
    return None


def _end(start: datetime, end: datetime | None) -> datetime | None:
    """MEC's end, a day late when it is one to two days after the start at a later clock."""
    if not end or end <= start:
        return None
    if timedelta(days=1) < end - start < timedelta(days=2) and end.timetz() > start.timetz():
        return end - timedelta(days=1)
    return end


def occurrences(item: icalendar.Event, start: datetime, now: datetime, days: int) -> list[datetime]:
    """The start, or for a repeating event its starts from now to ``days`` ahead."""
    rule, extra = item.get("RRULE"), item.get("RDATE")
    if not rule and not extra:
        return [start]
    rules = rruleset()
    if rule:
        rules.rrule(rrulestr(rule.to_ical().decode(), dtstart=start, ignoretz=False))  # type: ignore[arg-type]
    else:
        rules.rdate(start)
    for group in extra if isinstance(extra, list) else [extra] if extra else []:
        for value in getattr(group, "dts", []):
            if (when := _local(value.dt, str(start.tzinfo))) is not None:
                rules.rdate(when)
    return list(rules.between(now - timedelta(days=1), now + timedelta(days=days), inc=True))


def parse_vevent(item: icalendar.Event, s: NbnSettings, timezone: str, now: datetime) -> list[ScrapedEvent]:
    """One feed entry -> its events (one per occurrence); none when it is online or lacks a title, page or start."""
    title = clean_str(html_to_text(str(item.get("SUMMARY") or "")))
    url = clean_str(str(item.get("URL") or ""))
    uid = clean_str(str(item.get("UID") or ""))
    start = _local(item.decoded("DTSTART") if item.get("DTSTART") else None, timezone)
    if not title or not url or not uid or not start or start.year < 2000:  # MEC's 1970 placeholder dates
        return []
    categories = _categories(item)
    place = clean_str(html_to_text(str(item.get("LOCATION") or "")))
    if is_online(place, categories, s):
        return []
    end = _end(start, _local(item.decoded("DTEND") if item.get("DTEND") else None, timezone))
    length = end - start if end else None
    image = clean_str(str(item.get("ATTACH") or ""))
    host = item.get("ORGANIZER")
    organizer = clean_str(html_to_text(str(host.params.get("CN") or ""))) if host is not None else None
    words = [*s.declared]
    for name in categories:
        if name not in s.city_categories:  # an area, not a kind of event
            words += s.labels.get(name, [name])
    towns = [s.city_categories[c] for c in categories if c in s.city_categories]
    repeating = bool(item.get("RRULE") or item.get("RDATE"))
    # A date without a time: a date value, or MEC's all-day event (00:00 to the next day's 00:00 or no end).
    timed = isinstance(item.decoded("DTSTART"), datetime) and (
        start.timetz().replace(tzinfo=None) != datetime.min.time() or bool(end and end.timetz() != start.timetz())
    )
    base = uid.split("@", 1)[0].removeprefix("MEC-")
    events = []
    for when in occurrences(item, start, now, s.recurrence_days):
        events.append(
            ScrapedEvent(
                source=NbnScraper.name,
                source_event_id=f"{base}:{when:%Y%m%d%H%M}" if repeating else base,
                url=url,
                title=title,
                description=clean_str(unescape(str(item.get("DESCRIPTION") or ""))),
                start_at=when,
                end_at=when + length if length else None,
                time_known=timed,
                address=place,
                city=towns[0] if len(set(towns)) == 1 else None,
                organizer=organizer,
                source_genres=list(dict.fromkeys(w.lower() for w in words if w.strip())),
                images=[image] if image and image.startswith(("http://", "https://")) else [],
            )
        )
    return events


def _categories(item: icalendar.Event) -> list[str]:
    """The entry's categories. The feed separates them by commas without escaping the ones in a name
    ("Film, Television & Theater" comes as "Film" and " Television & Theater")."""
    raw = item.get("CATEGORIES")
    names: list[str] = []
    for group in raw if isinstance(raw, list) else [raw] if raw else []:
        for value in getattr(group, "cats", []) or str(group).split(","):
            if name := clean_str(html_to_text(str(value))):
                names.append(name)
    return names


def is_online(place: str | None, categories: list[str], s: NbnSettings) -> bool:
    if place:
        return any(re.search(rf"\b{re.escape(w)}\b", place.lower()) for w in s.online_words)
    return any(c in s.online_categories for c in categories)


def with_page(event: ScrapedEvent, page: str, s: NbnSettings) -> ScrapedEvent:
    """The event with its page's venue, address, organizer, cost and registration link."""
    node = next((n for n in jsonld_nodes(page) if "event" in schema_types(n)), {})
    location = node.get("location") if isinstance(node.get("location"), dict) else {}
    organizer = node.get("organizer") if isinstance(node.get("organizer"), dict) else {}
    venue = clean_str(html_to_text(str(location.get("name") or "")))
    address = clean_str(html_to_text(str(location.get("address") or ""))) or event.address
    if venue and address and (venue.lower() in address.lower() or any(c.isdigit() for c in venue)):
        venue = None  # the street again, not a name
    doc = LexborHTMLParser(page)
    update: dict[str, Any] = {
        "venue_name": venue if venue and not is_online(venue, [], s) else None,
        "address": address,
        "organizer": clean_str(html_to_text(str(organizer.get("name") or ""))) or event.organizer,
    }
    cost = doc.css_first(".mec-events-event-cost")
    if cost and (price := _price(cost.text())) is not None:
        currency = "USD" if "$" in cost.text() else "ILS"
        update |= {
            "currency": currency,
            "min_price": price,
            "max_price": price,
            "tickets": [
                TicketTier(name="Ticket" if price else "Free", price=price, currency=currency, is_free=not price)
            ],
        }
    button = doc.css_first("a.mec-booking-button") or doc.css_first("a.mec-more-info-button")
    link = clean_str(button.attributes.get("href")) if button else None
    if link and link.startswith(("http://", "https://")) and s.site_url.split("//", 1)[-1] not in link:
        update["extra"] = {**event.extra, "purchase_url": link}
    return event.model_copy(update=update)


def _price(text: str) -> Decimal | None:
    """MEC's cost line: "NIS165.00" -> 165; "Free" -> 0; anything else (a sentence) -> None."""
    text = text.strip()
    if text.lower() in {"free", "free entry", "free of charge"}:
        return Decimal(0)
    found = _AMOUNT.search(text.replace(",", ""))
    if not found or len(text) > 40:
        return None
    return Decimal(found.group(1))
