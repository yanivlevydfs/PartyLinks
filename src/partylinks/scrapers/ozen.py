"""Ozen Bar (ozentelaviv.com) - a Tel Aviv bar and club (King George 48): parties, live shows, stand-up and
listening sessions ("אוזן קשבת").

The site is WordPress with Tickera: its post type ``tc_events`` lists every event in one wp-json call (22 on
24.11.2026, 100 a page, read to the last page), with the start and end in ``meta`` (``event_date_time``,
``event_end_date_time``, local time), the site's category (``event_category``: parties, shows, stand-up,
lectures) and the artwork (``_embed``). The tickets are sold elsewhere: the text links the seller (Go-Out,
IWANT, Ticket Tailor), kept as the purchase link so the seller's own listing merges with it. No prices (the
WooCommerce products are past ticket types). One venue: its pin is in config.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import datetime
from html import unescape
from typing import Any
from urllib.parse import urlsplit

from pydantic import Field

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, html_to_text, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_HREF = re.compile(r"""href=["'](https?://[^"']+)["']""")


class OzenSettings(SourceSettings):
    site_url: str = "https://ozentelaviv.com"
    events_path: str = "/wp-json/wp/v2/tc_events?per_page={per_page}&page={page}&_embed=wp:featuredmedia,wp:term"
    per_page: int = 100  # WordPress's maximum
    max_pages: int = 50  # a runaway guard only: the site holds a few dozen posts
    start_key: str = "event_date_time"
    end_key: str = "event_end_date_time"
    # Hosts that sell the tickets: the first link to one of them is the purchase link.
    ticket_hosts: list[str] = Field(
        default_factory=lambda: ["go-out.co", "iwant.co.il", "tickettailor.com", "eventer.co.il", "tickchak.co.il"]
    )
    image_types: list[str] = Field(default_factory=lambda: [".jpg", ".jpeg", ".png", ".webp", ".gif"])
    venue_name: str = "אוזן בר"
    address: str = "קינג ג'ורג' 48"
    city: str = "תל אביב"
    latitude: float | None = None
    longitude: float | None = None


class OzenScraper(BaseScraper[OzenSettings]):
    name = "ozen"
    settings_model = OzenSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        posts: list[Any] = []
        for page in range(1, s.max_pages + 1):  # every page, to the last (a short one)
            batch = await self.get_json(s.site_url + s.events_path.format(per_page=s.per_page, page=page), expect=list)
            posts.extend(batch)
            if len(batch) < s.per_page:
                break
        events = []
        retention = RetentionSettings()
        for post in posts:
            try:
                event = parse_post(post, s, self.timezone)
            except Exception as exc:  # noqa: BLE001 - one odd post never drops the others
                self.record_failure(f"post {post.get('id') if isinstance(post, dict) else post!r}", exc)
                continue
            if event and is_current(event.start_at, event.end_at, retention, self.timezone):
                events.append(event)
        for event in sorted(events, key=lambda e: e.start_at or datetime.max.replace(tzinfo=zone(self.timezone))):
            yield event


def _local(value: object, timezone: str) -> datetime | None:
    try:
        return datetime.strptime(str(value or "").strip(), "%Y-%m-%d %H:%M").replace(tzinfo=zone(timezone))
    except ValueError:
        return None


def purchase_link(html: str, hosts: list[str]) -> str | None:
    """The first link in the text to a ticket seller (not Instagram, not the site itself)."""
    for link in _HREF.findall(html):
        host = (urlsplit(link).hostname or "").removeprefix("www.")
        if any(host == h or host.endswith("." + h) for h in hosts):
            return unescape(link)
    return None


def parse_post(post: dict[str, Any], s: OzenSettings, timezone: str) -> ScrapedEvent | None:
    """One ``tc_events`` post -> an event; None for a draft or a post without a title or start."""
    if post.get("status", "publish") != "publish" or not post.get("id"):
        return None
    meta = post.get("meta") or {}
    title = clean_str(unescape(str((post.get("title") or {}).get("rendered") or "")))
    start = _local(meta.get(s.start_key), timezone)
    if not title or start is None:
        return None
    end = _local(meta.get(s.end_key), timezone)
    if end is not None and end <= start:  # "2026-12-06 20:00" to "15:31": a slip of the date picker
        end = None
    embedded = post.get("_embedded") or {}
    media = next(iter(embedded.get("wp:featuredmedia") or []), {})
    image = clean_str(media.get("source_url")) if isinstance(media, dict) else None
    if image and not image.lower().endswith(tuple(s.image_types)):  # a PDF poster
        image = None
    terms = [t for group in embedded.get("wp:term") or [] for t in group if isinstance(t, dict)]
    genres = [str(t["name"]).strip().lower() for t in terms if t.get("taxonomy") == "event_category" and t.get("name")]
    artists = [clean_str(unescape(str(t["name"]))) for t in terms if t.get("taxonomy") == "artist" and t.get("name")]
    content = str((post.get("content") or {}).get("rendered") or "")
    link = purchase_link(content, s.ticket_hosts)
    return ScrapedEvent(
        source=OzenScraper.name,
        source_event_id=str(post["id"]),
        url=str(post.get("link") or ""),
        title=title,
        description=html_to_text(content),
        start_at=start,
        end_at=end,
        venue_name=s.venue_name,
        address=s.address,
        city=s.city,
        latitude=s.latitude,
        longitude=s.longitude,
        source_genres=genres,
        lineup=[a for a in artists if a],
        images=[image] if image else [],
        organizer="Ozen Bar",
        extra={"purchase_url": link} if link else {},
    )
