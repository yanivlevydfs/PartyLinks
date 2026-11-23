"""Yitzhak Rabin Center (rabincenter.org.il, "מרכז יצחק רבין"), Tel Aviv - the shows, artist meetings and family
days of its Israeli Museum ("המוזיאון הישראלי"): "כשבנאי פגש את אלוני", "כשוילנסקי פגש את דמארי", talks.

The site is WordPress (JetEngine). Its ``event`` post type is in wp-json: one call (``events_path``, 100 a page,
``_embed`` for the artwork) gives every post (~60, nearly all past) with its ACF text (``acf.text``), its
categories (``event_category``: events, running / past exhibitions, memorial day) and its page. The ACF date
fields are empty: the date is only in the text ("30.11.2026 / יום שני / 11:30"), so an event is the first full
date there (day.month.year) and the first clock after it. A post without a full date (an ongoing activity, a show
whose date is only in its artwork) is not imported. The text gives the price ("מחיר כרטיס: 60 ₪") and the
ticket link: the museum's AttractiNet box office (``web.attractinet.co.il/RabinCenter``), a stateful order form
that is linked, never read. The text's first line names the series ("תרבות ישראלית"): ``labels`` maps it to
the words our genres read. Exhibition-only posts (``exhibition_categories``) are no events. All at the center:
venue, address and point come from the config. robots.txt allows everything but /wp-admin/.
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

_DAY = re.compile(r"(?<![\d.])(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})(?![\d.])")
_CLOCK = re.compile(r"(?<![\d.:])([01]?\d|2[0-3]):([0-5]\d)(?![\d:])")
_HREF = re.compile(r'href="(https?://[^"]+)"')


class RabinCenterSettings(SourceSettings):
    site_url: str = "https://www.rabincenter.org.il"
    events_path: str = "/wp-json/wp/v2/event"
    per_page: int = Field(100, ge=1, le=100)
    max_pages: int = Field(3, ge=1)
    # event_category ids of exhibitions (running / past): a post in these alone is no event.
    exhibition_categories: list[int] = Field(default_factory=lambda: [10, 12])
    # Hosts whose link in the text is the ticket page.
    ticket_hosts: list[str] = Field(default_factory=lambda: ["attractinet.co.il"])
    # A series heading (the text's first line, "תרבות ישראלית") -> the words declared for it.
    labels: dict[str, list[str]] = Field(default_factory=dict)
    venue_name: str
    address: str
    city: str
    latitude: float | None = None
    longitude: float | None = None


class RabinCenterScraper(BaseScraper[RabinCenterSettings]):
    name = "rabincenter"
    settings_model = RabinCenterSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings

        async def page(index: int) -> tuple[list[dict[str, Any]], str | None]:
            params = {"per_page": s.per_page, "page": index + 1, "_embed": "wp:featuredmedia"}
            response = await self.request("GET", s.site_url + s.events_path, params=params)
            rows = response.json()
            return ([r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []), response.headers.get(
                "X-WP-Total"
            )

        posts = await self.paginate(page, s.per_page, s.max_pages)
        events: list[ScrapedEvent] = []
        for post in posts:
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
                events.append(event)
        self.log.debug("listed %d posts, %d coming events", len(posts), len(events))
        for event in sorted(events, key=lambda e: (e.start_at, e.source_event_id)):
            yield event  # nearest first


def parse_post(post: dict[str, Any], s: RabinCenterSettings, timezone: str) -> ScrapedEvent | None:
    """One ``event`` post -> its event; None without a title, a link or a full date in the text, or for an
    exhibition."""
    acf = post.get("acf") if isinstance(post.get("acf"), dict) else {}
    title = clean_str(unescape(str((post.get("title") or {}).get("rendered") or "")))
    categories = [c for c in post.get("event_category") or [] if isinstance(c, int)]
    if not post.get("id") or not post.get("link") or not title:
        return None
    if categories and set(categories) <= set(s.exhibition_categories):
        return None  # an exhibition, not an event
    raw = str(acf.get("text") or "")
    text = html_to_text(raw) or ""
    day = _DAY.search(text)
    if not day:
        return None  # no date in the text: nothing to place it on
    year = int(day.group(3)) + (2000 if len(day.group(3)) == 2 else 0)
    clock = _CLOCK.search(text, day.end())
    start = datetime.combine(
        date(year, int(day.group(2)), int(day.group(1))),
        time(int(clock.group(1)), int(clock.group(2))) if clock else time.min,
        tzinfo=zone(timezone),
    )
    extra: dict[str, Any] = {}
    if link := ticket_link(raw, s.ticket_hosts):
        extra["purchase_url"] = link
    media = (post.get("_embedded") or {}).get("wp:featuredmedia") or []
    images = [
        m["source_url"] for m in media if isinstance(m, dict) and str(m.get("source_url") or "").startswith("http")
    ]
    tickets = text_tickets(text)
    prices = [t.price for t in tickets if t.price is not None]
    heading = next((line.strip() for line in text.splitlines() if line.strip()), "")
    declared = [heading, *s.labels[heading]] if heading in s.labels else []
    return ScrapedEvent(
        source=RabinCenterScraper.name,
        source_event_id=str(post["id"]),
        url=str(post["link"]),
        title=title,
        description=text or None,
        start_at=start,
        time_known=bool(clock),
        venue_name=s.venue_name,
        address=s.address,
        city=s.city,
        latitude=s.latitude,
        longitude=s.longitude,
        source_genres=[d.lower() for d in declared],
        images=images,
        tickets=tickets,
        min_price=min(prices, default=None),
        max_price=max(prices, default=None),
        extra=extra,
    )


def ticket_link(html: str, hosts: list[str]) -> str | None:
    """The first link in the text to the box office (``hosts``), cut at its query's stray text."""
    for href in _HREF.findall(html):
        url = unescape(href)
        host = (urlparse(url).hostname or "").removeprefix("www.")
        if any(host == h or host.endswith("." + h) for h in hosts):
            # The site pastes words after the link: "...lang=heלהזמנות:". Keep the URL's own characters.
            url = re.sub(r"[^\x21-\x7e].*$", "", url)
            return re.sub(r"(?:%D7%[0-9A-F]{2})+.*$", "", url, flags=re.IGNORECASE)  # the same words, encoded
    return None
