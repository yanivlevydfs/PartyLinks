"""HaHalutz 33 / החלוץ 33 (halutz.co.il) - culture bar in Be'er Sheva's Old City, with a second stage in Sderot.

WordPress + Elementor. Every show is an ordinary post, and the site keeps only upcoming ones (31 posts in
October 2026, all ahead), so the REST API (``/wp-json/wp/v2/posts``) is the listing: title, text with the
Eventer iframe (the ticket link) and the ``loc`` term - 173 "p33" Be'er Sheva, 174 "p32" HaHalutz 32 Sderot.
The show's date and time are Elementor fields the API does not expose: they are read from the post's page
(``.date-text`` "2026-10-29" and "דלתות 20:00 ★ מופע 21:00", the Israeli clock), which also has the artwork.
No prices on the site (only inside Eventer) except "הכניסה חופשית" in the text of the free jazz nights. Places come from the config: the main venue plus ``branches``
keyed by ``loc`` term id.
"""

from __future__ import annotations

import html
import re
from collections.abc import AsyncIterator
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field
from selectolax.lexbor import LexborHTMLParser

from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import clean_str, html_to_text, og_image, zone
from partylinks.scrapers.base import BaseScraper, SourceSettings

_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_SHOW = re.compile(r"מופע\s*(\d{1,2}):(\d{2})")
_DOORS = re.compile(r"דלתות\s*(\d{1,2}):(\d{2})")
_SCRIPT = re.compile(r"<(script|style|iframe)\b.*?</\1>", re.DOTALL | re.IGNORECASE)
_EVENTER = re.compile(r"<iframe[^>]*?\ssrc=\"(https://www\.eventer\.co\.il/[A-Za-z0-9]+)")


class HalutzPlace(BaseModel):
    venue_name: str
    address: str
    city: str
    latitude: float | None = None
    longitude: float | None = None


class HalutzSettings(SourceSettings):
    site_url: str
    listing_path: str = "/wp-json/wp/v2/posts?per_page=100&_fields=id,link,title,content,loc"
    venue_name: str
    address: str
    city: str
    latitude: float | None = None
    longitude: float | None = None
    # Other stages by `loc` term id (174: Sderot); posts of any other term are at the main venue.
    branches: dict[int, HalutzPlace] = Field(default_factory=dict)


class HalutzScraper(BaseScraper[HalutzSettings]):
    name = "halutz"
    settings_model = HalutzSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        cards = parse_listing(await self.get_json(s.site_url + s.listing_path, expect=list))
        self.log.info("listed %d shows", len(cards))
        for card in cards:
            # The date is only on the page: without it there is no event.
            page = await self.optional(f"page {card['url']}", self.get_text(card["url"]))
            if not page:
                continue
            try:
                event = parse_card(card, page, s, self.timezone)
            except Exception as exc:  # noqa: BLE001 - one odd post never drops the others
                self.record_failure(f"show {card['url']}", exc)
                continue
            if event is not None:
                yield event


def parse_listing(posts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """REST posts -> one dict per show: id, url, title, text (HTML), loc term ids."""
    cards = []
    for post in posts:
        url = post.get("link") or ""
        if not url or not post.get("id"):
            continue
        cards.append({
            "id": str(post["id"]), "url": url,
            "title": clean_str(html.unescape((post.get("title") or {}).get("rendered") or "")) or "",
            "content": (post.get("content") or {}).get("rendered") or "", "loc": post.get("loc") or [],
        })  # fmt: skip
    return cards


def parse_card(card: dict[str, Any], page_html: str, s: HalutzSettings, timezone: str) -> ScrapedEvent | None:
    """A post + its page -> event; None without a date on the page or a title."""
    tree = LexborHTMLParser(page_html)
    single = tree.css_first(".elementor-location-single") or tree.root
    texts = [clean_str(n.text()) or "" for n in single.css(".elementor-icon-list-text")] if single else []
    day = next((date(int(m[1]), int(m[2]), int(m[3])) for t in texts if (m := _DATE.fullmatch(t))), None)
    if day is None or not card["title"]:
        return None
    clock = next((m for t in texts if (m := _SHOW.search(t) or _DOORS.search(t))), None)
    doors = next((m.group(0).split()[-1] for t in texts if (m := _DOORS.search(t))), None)
    hour, minute = (int(clock[1]), int(clock[2])) if clock else (0, 0)
    place: HalutzSettings | HalutzPlace = next((s.branches[t] for t in card["loc"] if t in s.branches), s)
    ticket = _EVENTER.search(card["content"])
    image = og_image(page_html)
    text = html_to_text(_SCRIPT.sub("", card["content"]))
    free = "כניסה חופשית" in (text or "")  # the free jazz nights have no ticket link
    return ScrapedEvent(
        source=HalutzScraper.name,
        source_event_id=card["id"],
        url=card["url"],
        title=card["title"],
        description=text,
        start_at=datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone(timezone)),
        venue_name=place.venue_name,
        address=place.address,
        city=place.city,
        latitude=place.latitude,
        longitude=place.longitude,
        source_genres=["הופעה"],
        images=[image] if image and image.startswith("http") else [],
        min_price=Decimal(0) if free else None,
        max_price=Decimal(0) if free else None,
        tickets=[TicketTier(name="כניסה חופשית", price=Decimal(0), is_free=True)] if free else [],
        extra={"doors_open": doors, **({"purchase_url": ticket.group(1)} if ticket else {})},
    )
