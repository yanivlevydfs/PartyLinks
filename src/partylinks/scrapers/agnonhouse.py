"""Agnon House (agnonhouse.org.il, "בית עגנון"), Talpiot, Jerusalem - S. Y. Agnon's house: lectures, literary
evenings and talks, in Hebrew and English.

The site is WordPress with its own ``event`` type, not in wp-json. ``/events/`` lists only the coming events
(~5, one request, soonest first): per card the day ("יום א' › י"ט בכסלו › 29.11.2026"), title, place and page.
Each event's page (one request each) gives the subtitle (``#event-titles h2``: "הרצאה מקוונת עם ד״ר ..."), the
date and time (``.detail.date``: "...<br>29.11.2026<br>18:00"), the place (``.detail.venue``), the artwork, the
post id (the body's ``postid-<n>``) and the text with the price ("מחיר: 30 ש״ח להרצאה בודדת"). Registration is
the page's own form, so the page is the ticket link. Online lectures (Zoom: "מקוונת", "בזום" in the title or
subtitle, ``online_words``) have no place: not imported. The place is the house (venue, address and point from
the config) unless the page names a place without the house's name. No categories: genres come from the text,
literature by default. robots.txt allows everything but /wp-admin/.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import date, datetime, time

from pydantic import Field
from selectolax.lexbor import LexborHTMLParser

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, html_to_text, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.text_prices import text_tickets

_DAY = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")
_POST_ID = re.compile(r"\bpostid-(\d+)\b")
_CLOCK = re.compile(r"(?<![\d.:])([01]?\d|2[0-3])[:.]([0-5]\d)(?![\d:.])")


class AgnonHouseSettings(SourceSettings):
    site_url: str = "https://agnonhouse.org.il"
    listing_path: str = "/events/"
    # The house's name as the place line writes it ("בית עגנון", "בית עגנון בירושלים").
    house_word: str = "עגנון"
    # Words of an online-only event in its title or subtitle.
    online_words: list[str] = Field(default_factory=lambda: ["מקוונ", "בזום", 'בזו"ם', "zoom", "online", "webinar"])
    venue_name: str
    address: str
    city: str
    latitude: float | None = None
    longitude: float | None = None


class AgnonHouseScraper(BaseScraper[AgnonHouseSettings]):
    name = "agnonhouse"
    settings_model = AgnonHouseSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        pages = listing_pages(await self.get_text(s.site_url + s.listing_path))
        self.log.debug("listed %d coming events", len(pages))

        async def read(url: str) -> ScrapedEvent | None:
            return parse_page(await self.get_text(url), url, s, self.timezone)

        async for event in self.stream_isolated(pages, read, label=lambda u: u):  # the listing's order: soonest first
            if event and is_current(
                event.start_at, event.end_at, RetentionSettings(), self.timezone, time_known=event.time_known
            ):
                yield event


def listing_pages(html: str) -> list[str]:
    """The coming events' pages, in the listing's order (soonest first), each once."""
    tree = LexborHTMLParser(html)
    urls = [(a.attributes.get("href") or "").strip() for a in tree.css(".event-block h2 a[href]")]
    return list(dict.fromkeys(u for u in urls if u.startswith(("http://", "https://"))))


def parse_page(html: str, url: str, s: AgnonHouseSettings, timezone: str) -> ScrapedEvent | None:
    """One event page -> its event; None without a title or a day, or for an online-only event."""
    tree = LexborHTMLParser(html)
    title = clean_str(_text(tree, "#event-titles h1"))
    subtitle = clean_str(_text(tree, "#event-titles h2"))
    when = _text(tree, ".detail.date", separator="\n")
    day = _DAY.search(when)
    if not title or not day:
        return None
    heading = f"{title} {subtitle or ''}".casefold()
    if any(w.casefold() in heading for w in s.online_words):
        return None  # a Zoom lecture: no place to put it
    after = when[day.end() :]
    clock = _CLOCK.search(after)
    start = datetime.combine(
        date(int(day.group(3)), int(day.group(2)), int(day.group(1))),
        time(int(clock.group(1)), int(clock.group(2))) if clock else time.min,
        tzinfo=zone(timezone),
    )
    place = clean_str(_text(tree, ".detail.venue"))
    at_house = not place or s.house_word in place
    body = tree.css_first("#event-content")
    text = html_to_text(body.html) if body and body.html else None
    description = "\n\n".join(t for t in (subtitle, text) if t) or None
    image = tree.css_first("#event-thumbnail-mob img[src], .wp-post-image[src]")
    images = [src] if image and (src := (image.attributes.get("src") or "").strip()).startswith("http") else []
    tickets = text_tickets(text)
    prices = [t.price for t in tickets if t.price is not None]
    post = _POST_ID.search(html)  # the body's class "postid-110600"
    key = post.group(1) if post else url.rstrip("/").rsplit("/", 1)[-1]
    return ScrapedEvent(
        source=AgnonHouseScraper.name,
        source_event_id=key,
        url=url,
        title=title,
        description=description,
        start_at=start,
        time_known=bool(clock),
        venue_name=s.venue_name if at_house else place,
        address=s.address if at_house else None,
        city=s.city,
        latitude=s.latitude if at_house else None,
        longitude=s.longitude if at_house else None,
        source_genres=[],
        images=images,
        tickets=tickets,
        min_price=min(prices, default=None),
        max_price=max(prices, default=None),
        extra={"purchase_url": url},  # registration is the page's own form
    )


def _text(tree: LexborHTMLParser, selector: str, separator: str = " ") -> str:
    node = tree.css_first(selector)
    if node is None:
        return ""
    for br in node.css("br"):
        br.replace_with("\n")
    return node.text(separator=separator)
