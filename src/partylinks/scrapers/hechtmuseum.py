"""Hecht Museum (mushecht.haifa.ac.il, "מוזיאון הכט"), University of Haifa - the "ארכיאולוגיה עכשיו" lecture
series, concerts (swing nights, the classical and organ series, family concerts) and stand-up evenings in the
museum's auditorium.

The site is WordPress. Its ``event`` post type is in wp-json: one call (``events_path``, 100 a page, ``_embed`` for
the artwork and the terms) gives every event post (~25, most past) with its ACF fields: the day (``acf.date``,
``YYYYMMDD``) and a one-line summary. The day is the listing's; the post text repeats the date (copied posts keep
an old one, so it is not read) and gives the time ("תחילת הרצאה 18:00 | התכנסות החל מ-17:30": the first clock is
the start), the price ("מחיר כרטיס: 35 ₪") and the Eventer purchase link (a short ``eventer.co.il/<code>``). The
``enentag`` terms are the series ("ארכיאולוגיה עכשיו", "לילות סווינג", "רביעי הגדול"); ``labels`` in the config
maps each to the words our genres read. A post copied for a new date can stay twice on the same day: one event per
title and day. Every event is in the auditorium: venue, address and point come from the config. robots.txt
allows everything.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import date, datetime, time
from html import unescape
from typing import Any
from urllib.parse import urlparse

from pydantic import Field

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, html_to_text, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.text_prices import text_tickets

_CLOCK = re.compile(r"(?<![\d.:])([01]?\d|2[0-3]):([0-5]\d)(?![\d:])")
_HREF = re.compile(r'href="(https?://[^"]+)"')
_DAY = re.compile(r"^(\d{4})(\d{2})(\d{2})$")


class HechtMuseumSettings(SourceSettings):
    site_url: str = "https://mushecht.haifa.ac.il"
    events_path: str = "/wp-json/wp/v2/event"
    per_page: int = Field(100, ge=1, le=100)
    max_pages: int = Field(5, ge=1)
    term_taxonomy: str = "enentag"  # the series terms
    # Hosts whose link in the post text is the event's ticket page.
    ticket_hosts: list[str] = Field(default_factory=lambda: ["eventer.co.il", "smarticket.co.il", "tickchak.co.il"])
    # A series term -> the words declared instead (our genres read them).
    labels: dict[str, list[str]] = Field(default_factory=dict)
    venue_name: str
    address: str
    city: str
    latitude: float | None = None
    longitude: float | None = None


class HechtMuseumScraper(BaseScraper[HechtMuseumSettings]):
    name = "hechtmuseum"
    settings_model = HechtMuseumSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings

        async def page(index: int) -> tuple[list[dict[str, Any]], str | None]:
            params = {"per_page": s.per_page, "page": index + 1, "_embed": "wp:featuredmedia,wp:term"}
            response = await self.request("GET", s.site_url + s.events_path, params=params)
            rows = response.json()
            return ([r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []), response.headers.get(
                "X-WP-Total"
            )

        posts = await self.paginate(page, s.per_page, s.max_pages)
        self.log.debug("listed %d event posts", len(posts))
        unique: dict[tuple[str, date], ScrapedEvent] = {}
        for post in sorted(posts, key=lambda p: int(p.get("id") or 0)):  # a newer copy replaces an older one
            try:
                event = parse_post(post, s, self.timezone)
            except Exception as exc:  # noqa: BLE001 - one odd post never drops the others
                self.record_failure(f"event {post.get('id')}", exc)
                continue
            if (
                event
                and event.start_at
                and is_current(
                    event.start_at, event.end_at, RetentionSettings(), self.timezone, time_known=event.time_known
                )
            ):
                unique[(event.title, event.start_at.date())] = event
        for event in sorted(unique.values(), key=lambda e: (e.start_at, e.source_event_id)):
            yield event  # nearest first


def parse_post(post: dict[str, Any], s: HechtMuseumSettings, timezone: str) -> ScrapedEvent | None:
    """One ``event`` post -> its event; None without an id, a link, a title or a day."""
    acf = post.get("acf") if isinstance(post.get("acf"), dict) else {}
    title = clean_str(unescape(str((post.get("title") or {}).get("rendered") or "")))
    day = _DAY.match(str(acf.get("date") or "").strip())
    if not post.get("id") or not post.get("link") or not title or not day:
        return None
    content = str((post.get("content") or {}).get("rendered") or "")
    text = html_to_text(content) or ""
    clock = _CLOCK.search(text)
    start = datetime.combine(
        date(int(day.group(1)), int(day.group(2)), int(day.group(3))),
        time(int(clock.group(1)), int(clock.group(2))) if clock else time.min,
        tzinfo=zone(timezone),
    )
    terms = _terms(post, s.term_taxonomy)
    declared: list[str] = []
    for name in terms:
        declared += [name, *s.labels.get(name, [])]
    extra: dict[str, Any] = {}
    if link := ticket_link(content, s.ticket_hosts):
        extra["purchase_url"] = link
    media = (post.get("_embedded") or {}).get("wp:featuredmedia") or []
    images = [
        m["source_url"] for m in media if isinstance(m, dict) and str(m.get("source_url") or "").startswith("http")
    ]
    tickets = text_tickets(text)
    prices = [t.price for t in tickets if t.price is not None]
    return ScrapedEvent(
        source=HechtMuseumScraper.name,
        source_event_id=str(post["id"]),
        url=str(post["link"]),
        title=title,
        description=text or clean_str(acf.get("description")),
        start_at=start,
        time_known=bool(clock),
        venue_name=s.venue_name,
        address=s.address,
        city=s.city,
        latitude=s.latitude,
        longitude=s.longitude,
        source_genres=list(dict.fromkeys(g.lower() for g in declared)),
        images=images,
        tickets=tickets,
        min_price=min(prices, default=None),
        max_price=max(prices, default=None),
        extra=extra,
    )


def ticket_link(content: str, hosts: list[str]) -> str | None:
    """The first link in the post text to a ticket seller (``hosts``)."""
    for href in _HREF.findall(content):
        host = (urlparse(unescape(href)).hostname or "").removeprefix("www.")
        if any(host == h or host.endswith("." + h) for h in hosts):
            return unescape(href)
    return None


def _terms(post: dict[str, Any], taxonomy: str) -> list[str]:
    """The names of the post's embedded terms in ``taxonomy``."""
    names: list[str] = []
    for group in (post.get("_embedded") or {}).get("wp:term") or []:
        for term in group if isinstance(group, list) else []:
            if (
                isinstance(term, dict)
                and term.get("taxonomy") == taxonomy
                and (name := clean_str(unescape(str(term.get("name") or "")).replace("\xa0", " ")))
            ):
                names.append(name)
    return names
