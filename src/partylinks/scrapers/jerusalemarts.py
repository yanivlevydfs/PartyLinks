"""Jerusalem Arts Festival (jerusalemarts.co.il, "פסטיבל ירושלים לאמנויות") - two December evenings of
contemporary theatre, dance, performance and music (2026: 2-3.12), at the Jerusalem Arts Campus (מנורה 3) and
Nocturno (בצלאל 7). An initiative of the city of Jerusalem.

The site is WordPress + Elementor; every show is a post. One wp-json call lists them all (``posts_path``;
``X-WP-Total``, 15 posts in 2026), and each post's ``content`` carries the show's own schema.org ``Event``
(``<script id="jaf-ld">``): name (with the festival's name after it), start and end (local, with offset),
the hall and its street, price, artwork, and the seller (``offers.url``: Eventer or Tickchak; a free show
points back at the site). The post's tags are the site's genres ("תיאטרון", "מחול", "מוזיקה",
"פרפורמנס"), named by one more call (``tags_path``); the categories are only the festival days.

Halls of one building are one venue (``places``, by street: the campus's halls; the hall kept in
``extra["hall"]``). A street not in ``places`` keeps its own hall as the venue, and its address for geocoding.
Between festivals the site lists only past shows: a run with nothing current is healthy.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import datetime
from decimal import Decimal
from html import unescape
from typing import Any
from urllib.parse import quote, urlparse

from pydantic import BaseModel, Field
from selectolax.lexbor import LexborHTMLParser

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import (
    clean_str,
    html_to_text,
    is_site_logo,
    jsonld_nodes,
    parse_iso,
    schema_types,
    to_decimal,
    zone,
)
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings


class JerusalemArtsPlace(BaseModel):
    venue_name: str
    latitude: float | None = None
    longitude: float | None = None


class JerusalemArtsSettings(SourceSettings):
    site_url: str = "https://jerusalemarts.co.il"
    posts_path: str = "/wp-json/wp/v2/posts"
    tags_path: str = "/wp-json/wp/v2/tags?per_page=100&_fields=id,name"
    per_page: int = 100
    max_pages: int = 5
    post_fields: str = "id,link,title,content,tags"
    # Street address (as the Event's PostalAddress writes it) -> the building its halls are in.
    places: dict[str, JerusalemArtsPlace] = Field(default_factory=dict)
    default_city: str = "ירושלים"
    organizer: str = "פסטיבל ירושלים לאמנויות"
    # The festival's name after the show's in the Event's name ("R U OK? – פסטיבל ירושלים לאמנויות 2026").
    title_suffix: str = r"\s+[–—-]\s+פסטיבל ירושלים לאמנויות(?:\s+\d{4})?\s*$"
    # Every show is a festival show (the festival genre, beside the tags' own).
    source_genres: list[str] = Field(default_factory=lambda: ["פסטיבל"])
    ticket_name: str = "כרטיס"
    free_ticket_name: str = "כניסה חופשית"
    # Paragraphs of the text that are not the show's description (the artists' Instagram line).
    drop_selectors: list[str] = Field(default_factory=lambda: ["script", "style", ".jaf-ig"])


class JerusalemArtsScraper(BaseScraper[JerusalemArtsSettings]):
    name = "jerusalemarts"
    settings_model = JerusalemArtsSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings

        async def page(index: int) -> tuple[list[dict[str, Any]], str | None]:
            params = {"per_page": s.per_page, "page": index + 1, "_fields": s.post_fields}
            response = await self.request("GET", s.site_url + s.posts_path, params=params)
            rows = response.json()
            return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else [], response.headers.get(
                "X-WP-Total"
            )

        posts = await self.paginate(page, s.per_page, s.max_pages)
        tags = await self.optional("tags", self.get_json(s.site_url + s.tags_path, expect=list)) or []
        names = {t.get("id"): clean_str(unescape(str(t.get("name") or ""))) for t in tags if isinstance(t, dict)}
        self.log.debug("listed %d posts", len(posts))
        events = []
        for post in posts:
            try:
                event = parse_post(post, names, s, self.timezone)
            except Exception as exc:  # noqa: BLE001 - one odd post never drops the others
                self.record_failure(f"post {post.get('id')}", exc)
                continue
            if event and is_current(event.start_at, event.end_at, RetentionSettings(), self.timezone):
                events.append(event)
        for event in sorted(events, key=lambda e: e.start_at or datetime.max.replace(tzinfo=zone(self.timezone))):
            yield event  # nearest first


def post_event(content_html: str) -> dict[str, Any] | None:
    """The schema.org ``Event`` in a post's content (``<script id="jaf-ld">``)."""
    return next((n for n in jsonld_nodes(content_html) if "event" in schema_types(n)), None)


def parse_post(
    post: dict[str, Any], tags: dict[Any, str | None], s: JerusalemArtsSettings, timezone: str
) -> ScrapedEvent | None:
    """One post -> its show; None without an Event, a start, a title or a link."""
    content = str((post.get("content") or {}).get("rendered") or "")
    event = post_event(content)
    if event is None or not post.get("id") or not post.get("link"):
        return None
    start = parse_iso(event.get("startDate"), timezone)
    end = parse_iso(event.get("endDate"), timezone)
    title = _title(event, post, s)
    if start is None or not title:
        return None
    location = event.get("location") if isinstance(event.get("location"), dict) else {}
    address = location.get("address") if isinstance(location.get("address"), dict) else {}
    hall = clean_str(location.get("name"))
    street = clean_str(address.get("streetAddress"))
    city = clean_str(address.get("addressLocality")) or s.default_city
    place = s.places.get(street or "")
    offer = event.get("offers") if isinstance(event.get("offers"), dict) else {}
    price = to_decimal(offer.get("price"))
    free = event.get("isAccessibleForFree") is True or price == 0
    if free:
        tickets = [TicketTier(name=s.free_ticket_name, price=Decimal(0), is_free=True)]
    else:
        tickets = [TicketTier(name=s.ticket_name, price=price)] if price is not None else []
    seller = _seller(clean_str(offer.get("url")), s)
    images = [i for i in _images(event.get("image")) if not is_site_logo(i)]
    genres = [*(g.lower() for g in s.source_genres), *(g.lower() for i in post.get("tags") or [] if (g := tags.get(i)))]
    performer = event.get("performer") if isinstance(event.get("performer"), dict) else {}
    extra: dict[str, Any] = {}
    if seller:
        extra["purchase_url"] = seller
    if hall and place and not hall.startswith(place.venue_name):  # "נוקטורנו, בצלאל 7" is Nocturno itself
        extra["hall"] = hall
    if name := clean_str(performer.get("name")):
        extra["performer"] = name
    return ScrapedEvent(
        source=JerusalemArtsScraper.name,
        source_event_id=str(post["id"]),
        url=str(post["link"]),
        title=title,
        description=_description(content, s) or clean_str(event.get("description")),
        start_at=start,
        end_at=end if end and end > start else None,
        venue_name=place.venue_name if place else hall,
        address=f"{street}, {city}" if street else None,
        city=city,
        latitude=place.latitude if place else None,
        longitude=place.longitude if place else None,
        source_genres=list(dict.fromkeys(genres)),
        images=images,
        tickets=tickets,
        min_price=Decimal(0) if free else price,
        max_price=Decimal(0) if free else price,
        organizer=s.organizer,
        extra=extra,
    )


def _title(event: dict[str, Any], post: dict[str, Any], s: JerusalemArtsSettings) -> str | None:
    """The Event's name without the festival's; else the post's title ("<br>" between its lines)."""
    name = clean_str(re.sub(s.title_suffix, "", str(event.get("name") or "")))
    if name:
        return name
    rendered = str((post.get("title") or {}).get("rendered") or "")
    return clean_str(" – ".join(p for p in (clean_str(unescape(x)) for x in re.split(r"<br\s*/?>", rendered)) if p))


def _description(content_html: str, s: JerusalemArtsSettings) -> str | None:
    tree = LexborHTMLParser(f"<div>{content_html}</div>")
    for selector in s.drop_selectors:
        for node in tree.css(selector):
            node.decompose()
    root = tree.css_first("div")
    return html_to_text(root.inner_html) if root is not None else None


def _seller(url: str | None, s: JerusalemArtsSettings) -> str | None:
    """The offer's link when it leads to a seller; a free show's offer points back at the festival site."""
    if not url or not url.startswith(("http://", "https://")):
        return None
    host = urlparse(url).netloc.lower().removeprefix("www.")
    site = urlparse(s.site_url).netloc.lower().removeprefix("www.")
    return None if host == site else url


def _images(value: Any) -> list[str]:
    items = value if isinstance(value, list) else [value]
    urls = [str(i.get("url") if isinstance(i, dict) else i or "").strip() for i in items]
    # Hebrew file names ("דימוי-מרכזי-....jpeg") percent-encoded, as og_image gives them.
    return [quote(u, safe=":/%?=&") for u in dict.fromkeys(urls) if u.startswith(("http://", "https://"))]
