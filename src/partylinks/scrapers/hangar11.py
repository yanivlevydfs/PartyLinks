"""Hangar 11 (hangar11.co.il), the Tel Aviv Port's big concert hall: shows, stand-up, parties and
match screenings in its two halls (the main hangar and the Hangar Club).

WordPress + The Events Calendar 4.9 with its REST API switched off (``/wp-json/`` is 404), so:

* ``/events/list/?ical=1``: the list view's iCal feed, every upcoming event in one request, soonest
  first: title, local start / end, description, image, event page URL, hall (``LOCATION``) and the
  hall's point (``GEO``). Its ``CATEGORIES`` are useless (every event carries the site's ~50 tags,
  from football to fashion week), so genres come from the title and description.
* Each event page (one request each): the ticket button (``#tickets`` - usually toMix, Eventer or
  Megatickets) and the price range (``.tribe-events-cost``, "169₪ – 259₪"). Its JSON-LD offer says
  USD and is not used.

robots.txt allows everything with ``Crawl-Delay: 20``: one request at a time, 20 s apart (~4 min for
~12 events). Both halls are one building: one venue, the hall kept in ``extra["hall"]``.
"""

from __future__ import annotations

import html
import re
from collections.abc import AsyncIterator
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

from icalendar import Calendar
from pydantic import Field
from selectolax.lexbor import LexborHTMLParser

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import ParseError, clean_str, html_to_text, to_decimal, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_HREF = re.compile(r'<a\s[^>]*href="(https?://[^"]+)"')
_PRICE = re.compile(r"(\d+(?:[.,]\d+)?)\s*₪|₪\s*(\d+(?:[.,]\d+)?)")
_UID = re.compile(r"^(\d+)-")
_COPY = re.compile(r"\s+copy$", re.IGNORECASE)  # a duplicated draft's title ("... Copy")


class Hangar11Settings(SourceSettings):
    site_url: str = "https://hangar11.co.il"
    feed_path: str = "/events/list/?ical=1"
    venue_name: str = "האנגר 11"
    address: str | None = None
    city: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    # The ticket button follows the "#tickets" menu anchor (a sibling, not a child).
    tickets_anchor: str = 'id="tickets"'
    tickets_window: int = Field(4000, ge=100)  # characters after the anchor where the button sits
    cost_selector: str = ".tribe-events-cost"
    # Links in the ticket block that are no seller.
    non_ticket_hosts: list[str] = Field(
        default_factory=lambda: ["hangar11.co.il", "facebook.com", "instagram.com", "wa.me", "whatsapp.com"]
    )


class Hangar11Scraper(BaseScraper[Hangar11Settings]):
    name = "hangar11"
    settings_model = Hangar11Settings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        retention = RetentionSettings()
        events = [
            e
            for e in parse_feed(await self.get_text(s.site_url + s.feed_path), s, self.timezone)
            if is_current(e.start_at, e.end_at, retention, self.timezone, time_known=e.time_known)
        ]
        events.sort(key=lambda e: e.start_at or datetime.max.replace(tzinfo=zone(self.timezone)))
        self.log.debug("feed lists %d upcoming events", len(events))
        for event in events:  # one at a time: robots.txt asks for 20 s between requests
            page = await self.optional(f"event page {event.url}", self.get_text(str(event.url)))
            yield with_page(event, page, s) if page else event


def _when(value: Any, timezone: str) -> tuple[datetime | None, bool]:
    """An iCal DTSTART / DTEND as an aware local datetime, and whether it has a time of day."""
    if value is None:
        return None, True
    dt = value.dt
    if isinstance(dt, datetime):
        return (dt if dt.tzinfo else dt.replace(tzinfo=zone(timezone))), True
    if isinstance(dt, date):
        return datetime.combine(dt, time(), tzinfo=zone(timezone)), False
    return None, True


def parse_feed(ics: str, settings: Hangar11Settings, timezone: str) -> list[ScrapedEvent]:
    events = []
    for component in Calendar.from_ical(ics).walk("VEVENT"):
        title = clean_str(_COPY.sub("", str(component.get("SUMMARY") or "")))
        url = clean_str(str(component.get("URL") or ""))
        start_at, time_known = _when(component.get("DTSTART"), timezone)
        end_at, _ = _when(component.get("DTEND"), timezone)
        if not title or not url or start_at is None:
            raise ParseError(f"feed event without title, URL or start: {component.get('UID')}")
        uid = str(component.get("UID") or "")
        post_id = _UID.match(uid)
        geo = component.get("GEO")
        image = component.get("ATTACH")
        hall = clean_str(str(component.get("LOCATION") or "").split(",")[0])
        events.append(
            ScrapedEvent(
                source=Hangar11Scraper.name,
                source_event_id=post_id.group(1) if post_id else uid or url,
                url=url,
                title=title,
                description=clean_str(str(component.get("DESCRIPTION") or "")),
                start_at=start_at,
                end_at=end_at if end_at and end_at > start_at else None,  # the site repeats the start
                time_known=time_known,
                venue_name=settings.venue_name,
                address=settings.address,
                city=settings.city,
                latitude=geo.latitude if geo else settings.latitude,
                longitude=geo.longitude if geo else settings.longitude,
                images=[str(image)] if image and str(image).startswith("http") else [],
                extra={"hall": hall} if hall else {},
            )
        )
    return events


def ticket_link(page_html: str, settings: Hangar11Settings) -> str | None:
    """The first seller link after the "Buy tickets" anchor."""
    start = page_html.find(settings.tickets_anchor)
    if start < 0:
        return None
    for href in _HREF.findall(page_html[start : start + settings.tickets_window]):
        host = (urlsplit(href).hostname or "").removeprefix("www.")
        if not any(host == h or host.endswith("." + h) for h in settings.non_ticket_hosts):
            return html.unescape(href)
    return None


def page_prices(text: str | None) -> list[Decimal]:
    """Shekel amounts written in the cost line ("169₪ – 259₪")."""
    prices = [to_decimal(a or b) for a, b in _PRICE.findall(text or "")]
    return [p for p in prices if p is not None and p > 0]


def with_page(event: ScrapedEvent, page_html: str, settings: Hangar11Settings) -> ScrapedEvent:
    """The feed event plus its page's ticket link and price range."""
    page = LexborHTMLParser(page_html)
    extra = dict(event.extra)
    ticket = ticket_link(page_html, settings)
    if ticket:
        extra["purchase_url"] = ticket
    cost = page.css_first(settings.cost_selector)
    prices = page_prices(html_to_text(cost.html) if cost else None)
    update: dict[str, Any] = {"extra": extra}
    if prices:
        update |= {"min_price": min(prices), "max_price": max(prices)}
    return event.model_copy(update=update)
