"""FeelBeit (feelbeit.com) - a Jerusalem arts space (4 Naomi St., the Planet complex) and its yearly LAYERS music
festival (שכבות): a few free nights of concerts and DJ sets.

The festival is a WordPress site of its own per year (``/layers2026/``, in Hebrew, English and Arabic). Its
custom post type ``layers_event`` lists every show in one wp-json call (``_embed`` for the artwork): the post's
``date`` is the show's start, local time; the title, the text (``content``) and the language (the link's
``/he/`` / ``/en/`` / ``/ar/``). Each show is three posts, one per language: the Hebrew ones are kept. Entry is
free for every show and there is no seller. One venue: its pin is in config. The main site's robots.txt sends
to a holding page; the festival site has none.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from html import unescape
from typing import Any

from pydantic import Field

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import clean_str, html_to_text, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings


class FeelBeitSettings(SourceSettings):
    site_url: str = "https://feelbeit.com/layers2026"  # this year's festival site
    events_path: str = "/wp-json/wp/v2/layers_event?per_page=100&_embed=wp:featuredmedia"
    language_path: str = "/he/"  # the Hebrew posts (one post per show and language)
    venue_name: str = "FeelBeit"
    address: str = "נעמי 4"
    city: str = "ירושלים"
    latitude: float | None = None
    longitude: float | None = None
    source_genres: list[str] = Field(default_factory=lambda: ["פסטיבל", "מוזיקה"])
    free_tier: str = "כניסה חופשית"


class FeelBeitScraper(BaseScraper[FeelBeitSettings]):
    name = "feelbeit"
    settings_model = FeelBeitSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        posts = await self.get_json(s.site_url + s.events_path, expect=list)
        events = []
        for post in posts:
            try:
                event = parse_post(post, s, self.timezone)
            except Exception as exc:  # noqa: BLE001 - one odd post never drops the others
                self.record_failure(f"post {post.get('id') if isinstance(post, dict) else post!r}", exc)
                continue
            if event and is_current(event.start_at, event.end_at, RetentionSettings(), self.timezone):
                events.append(event)
        for event in sorted(events, key=lambda e: e.start_at or datetime.max.replace(tzinfo=zone(self.timezone))):
            yield event


def parse_post(post: dict[str, Any], s: FeelBeitSettings, timezone: str) -> ScrapedEvent | None:
    """One ``layers_event`` post -> a show; None for another language's copy, a draft or a post without a date."""
    link = str(post.get("link") or "")
    if s.language_path not in link or post.get("status", "publish") != "publish":
        return None
    title = clean_str(unescape(str((post.get("title") or {}).get("rendered") or "")))
    try:
        start = datetime.fromisoformat(str(post.get("date"))).replace(tzinfo=zone(timezone))
    except ValueError:
        return None
    if not title or not post.get("id"):
        return None
    media = next(iter((post.get("_embedded") or {}).get("wp:featuredmedia") or []), {})
    image = clean_str(media.get("source_url")) if isinstance(media, dict) else None
    return ScrapedEvent(
        source=FeelBeitScraper.name,
        source_event_id=str(post["id"]),
        url=link,
        title=title,
        description=html_to_text(str((post.get("content") or {}).get("rendered") or "")),
        start_at=start,
        venue_name=s.venue_name,
        address=s.address,
        city=s.city,
        latitude=s.latitude,
        longitude=s.longitude,
        source_genres=list(s.source_genres),
        images=[image] if image else [],
        tickets=[TicketTier(name=s.free_tier, price=0, is_free=True)],
        currency="ILS",
        min_price=0,
        max_price=0,
        organizer="FeelBeit",
    )
