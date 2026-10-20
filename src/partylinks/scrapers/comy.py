"""Comy (comy.co.il) - stand-up comedy box office (WordPress site, own theme).

Ticketing runs on PRES (``tickets.comy.co.il``), but that host's robots.txt disallows
everything, so only the WordPress site is read. Its REST API has no show data
(``/wp-json/comy/v1/shows`` is a POST hook) and ``event-sitemap.xml`` is mostly dead
pages, so the listing is the home page: every ``/event/<slug>/`` link on it.
Details: each show page has the artist / show name (``h1`` / ``h2``), artwork, synopsis,
the run's date range (``.te-date``: ``dd/mm/yyyy - dd/mm/yyyy``), an optional price line
(``.ticket-price``) and one ``a.single-page-event`` per performance: ``dd.mm`` date,
``HH:MM`` time, a free-text place ("בית החייל - תל אביב"; the city is read from it by
enrichment), the purchase link (PRES ``?prsntId=``, Gray Club or a foreign shop; ``#``
when sold out or postponed) and a sold-out badge.
The year is not printed per performance: performances are in date order from the range start.
One ``ScrapedEvent`` per performance; ``source_event_id`` is ``<slug>:<start>`` (rows sometimes
share one PRES presentation id by mistake, so that id is kept in ``extra`` only).
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import date, datetime
from zoneinfo import ZoneInfo

from pydantic import Field
from selectolax.lexbor import LexborHTMLParser, LexborNode

from partylinks.models import ScrapedEvent
from partylinks.parsing import ParseError, clean_str, html_to_text, og_image, to_decimal
from partylinks.scrapers.base import BaseScraper, SourceSettings

_RANGE_START = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")
_DAY = re.compile(r"(\d{1,2})\.(\d{1,2})")
_TIME = re.compile(r"(\d{1,2}):(\d{2})")
_PRICE = re.compile(r"\d+(?:\.\d+)?")
_PRESENTATION = re.compile(r"[?&]prsntId=(\d+)")


class ComySettings(SourceSettings):
    site_url: str
    listing_path: str = "/"
    event_page_prefix: str = "/event/"
    max_events: int = Field(300, ge=1)


class ComyScraper(BaseScraper[ComySettings]):
    name = "comy"
    settings_model = ComySettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        pages = listing_pages(await self.get_text(s.site_url + s.listing_path), s.site_url + s.event_page_prefix)
        self.log.info("listed %d show pages", len(pages))
        today = datetime.now(ZoneInfo(self.timezone)).date()

        async def show(url: str) -> list[ScrapedEvent]:
            return parse_show(await self.get_text(url), url, self.timezone, today)

        async for events in self.stream_isolated(pages[: s.max_events], show, label=lambda url: url):
            for event in events:
                yield event


def listing_pages(page_html: str, prefix: str) -> list[str]:
    """Show page URLs linked from the home page, in order, without duplicates."""
    found = re.findall(re.escape(prefix) + r'[^"/?#\s]+/', page_html)
    return list(dict.fromkeys(found))


def parse_show(page_html: str, url: str, timezone: str, today: date) -> list[ScrapedEvent]:
    tree = LexborHTMLParser(page_html)
    show = tree.css_first("#the-event")
    if show is None:
        raise ParseError("show block (#the-event) not found")
    title = _text(show, "h1")
    if not title:
        raise ParseError("show title missing")
    kind = _text(show, "h2")
    img = show.css_first(".te-image img[src]")
    image = (img.attributes.get("src") if img else None) or og_image(page_html)
    synopsis = show.css_first(".te-details > p")
    description = html_to_text(synopsis.html) if synopsis else None
    price_match = _PRICE.search(_text(show, ".ticket-price") or "")
    price = to_decimal(price_match.group(0)) if price_match else None
    range_match = _RANGE_START.search(_text(show, ".te-date") or "")
    anchor = date(int(range_match[3]), int(range_match[2]), int(range_match[1])) if range_match else today
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    tz = ZoneInfo(timezone)

    events: list[ScrapedEvent] = []
    for row in tree.css("#events a.single-page-event"):
        day = _DAY.search(_text(row, ".date") or "")
        clock = _TIME.search(_text(row, ".single-light") or "")
        if not day or not clock:
            continue
        month, dom = int(day[2]), int(day[1])
        # No year on the row: the run's first date anchors it; earlier month/day = next year.
        year = anchor.year + ((month, dom) < (anchor.month, anchor.day))
        start = datetime(year, month, dom, int(clock[1]), int(clock[2]), tzinfo=tz)
        href = row.attributes.get("href") or ""
        presentation = _PRESENTATION.search(href)
        events.append(
            ScrapedEvent(
                source=ComyScraper.name,
                source_event_id=f"{slug}:{start:%Y%m%d%H%M}",
                url=href if href.startswith("http") else url,
                title=title,
                description=description,
                start_at=start,
                venue_name=_text(row, ".single-place-string"),
                source_genres=[kind] if kind else [],
                image_url=image,
                min_price=price,
                max_price=price,
                is_sold_out=row.css_first(".event-sold-out") is not None,
                extra={"show_page": url, "presentation_id": presentation[1] if presentation else None},
            )
        )
    return events


def _text(node: LexborNode, selector: str) -> str | None:
    found = node.css_first(selector)
    return clean_str(found.text()) if found else None
