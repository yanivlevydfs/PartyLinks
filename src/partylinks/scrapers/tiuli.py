"""Tiuli (tiuli.com) - Israeli trips and leisure guide; its events section.

robots.txt disallows ``/search``, ``/ajax``, ``/map``, ``/content`` and the like; ``/events`` and
the event / article pages are allowed. There is no JSON API, the events sitemap is stale (5 URLs)
and the pages carry no schema.org ``Event`` (only a ``Product`` with name and image), so:

* Listing: the ``/events`` page holds the whole catalogue (~10 cards, no pagination). A card is
  an ``/events/<id>`` or ``/articles/<id>`` page (events written up as articles are listed too);
  only cards with a "תאריכים:" line are events. A card gives title, category, teaser, image, the
  dates (``dd/mm``, no year: one date, several ``dd/mm , dd/mm`` or a run ``dd/mm עד dd/mm``) and
  the place as "locality | sub-region | region" (sometimes only a region, e.g. "השפלה").
* Details: the page is editorial prose. Read from it: the seller / organizer link (the body's
  external link ending in "»", e.g. "לפרטים ולהזמנת כרטיסים »"; organizers' own sites, parks
  authority, sometimes Ticketmaster), the "איפה:" line as the address, a start time
  ("החל מהשעה 19:30") for single-date events, and the ``og:image``.

One event per date; a run is one event from today (or its first day) to its last day.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import date, datetime, time, timedelta
from typing import NamedTuple
from urllib.parse import urlsplit

from pydantic import Field
from selectolax.lexbor import LexborHTMLParser, LexborNode

from partylinks.models import ScrapedEvent
from partylinks.parsing import ParseError, clean_str, html_to_text, og_image, zone
from partylinks.scrapers.base import BaseScraper, SourceSettings

_DAY = re.compile(r"(\d{1,2})/(\d{1,2})")
_PAGE_ID = re.compile(r"/(events|articles)/(\d+)")
_START_TIME = re.compile(r"(?:החל\s+מהשעה|בשעה|בין\s+השעות)\s*(\d{1,2}):(\d{2})")
_WHERE = re.compile(r"איפה:\s*</span>(.*?)</li>", re.DOTALL)
# A date this far behind today is next year's (the card prints no year).
_PAST_GRACE = timedelta(days=90)


class TiuliSettings(SourceSettings):
    site_url: str
    list_path: str  # /events
    max_events: int = Field(200, ge=1)


class Card(NamedTuple):
    url: str
    page_id: str  # "events-15087" / "articles-3124"
    title: str
    category: str | None
    teaser: str | None
    image: str | None
    dates: str  # "29/10" / "29/10 , 30/10" / "17/11 עד 31/01"
    places: list[str]  # locality first, then the regions


class TiuliScraper(BaseScraper[TiuliSettings]):
    name = "tiuli"
    settings_model = TiuliSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        cards = parse_listing(await self.get_text(s.site_url + s.list_path))
        if len(cards) > s.max_events:
            self.log.warning("listing truncated at max_events=%d (%d found)", s.max_events, len(cards))
        self.log.info("listed %d dated cards", len(cards))
        today = datetime.now(zone(self.timezone)).date()

        async def fetch(card: Card) -> list[ScrapedEvent]:
            # The card alone is enough for an event: a failed page only loses link / address / time.
            page = await self.optional(f"page {card.url}", self.get_text(card.url))
            return build_events(card, page, today, self.timezone)

        async for events in self.stream_isolated(cards[: s.max_events], fetch, label=lambda c: c.url):
            for event in events:
                yield event


def _text(node: LexborNode | None) -> str | None:
    return clean_str(node.text(separator=" ")) if node else None


def parse_listing(page_html: str) -> list[Card]:
    """The dated event cards of the ``/events`` page, each page once."""
    cards: dict[str, Card] = {}
    for article in LexborHTMLParser(page_html).css("article"):
        link = article.css_first("h2 a[href]")
        clock = article.css_first(".icon-clock")
        url = link.attributes.get("href") if link else None
        page = _PAGE_ID.search(url or "")
        if not link or not url or not page or clock is None or clock.parent is None:
            continue
        dates = clock.parent.next  # the label's sibling <div class="flex-1"> holds the date spans
        while dates is not None and dates.tag != "div":
            dates = dates.next
        marker = article.css_first(".icon-map-marker")
        place = marker.next if marker else None
        while place is not None and place.tag != "span":
            place = place.next
        img = article.css_first("img[src]")
        title = _text(link)
        if not title or dates is None:
            continue
        cards.setdefault(
            url,
            Card(
                url=url,
                page_id=f"{page[1]}-{page[2]}",
                title=title,
                category=_text(article.css_first('a[href*="types="]')),
                teaser=_text(article.css_first("p")),
                image=img.attributes.get("src") if img else None,
                dates=_text(dates) or "",
                places=[p for p in (clean_str(x) for x in (_text(place) or "").split("|")) if p],
            ),
        )
    return list(cards.values())


def _resolve(day: int, month: int, today: date, year: int | None = None) -> date:
    """dd/mm in ``year``; without one, the next occurrence (up to ``_PAST_GRACE`` back)."""
    if year is not None:
        return date(year, month, day)
    found = date(today.year, month, day)
    return date(today.year + 1, month, day) if found < today - _PAST_GRACE else found


def card_dates(text: str, today: date) -> tuple[list[date], date | None]:
    """(start dates, run end): one date per performance, or a run's first day and its last."""
    days = [(int(d), int(m)) for d, m in _DAY.findall(text)]
    if "עד" in text and len(days) == 2:
        end = _resolve(*days[1], today)
        start = _resolve(*days[0], today, end.year)
        return [start if start <= end else start.replace(year=end.year - 1)], end
    return sorted({_resolve(d, m, today) for d, m in days}), None


def _purchase_url(body: LexborNode | None) -> str | None:
    """The body's outbound link: the "... »" call to action, else the first external one."""
    links = [
        (a.attributes.get("href") or "", _text(a) or "")
        for a in (body.css("a[href]") if body else [])
        if urlsplit(a.attributes.get("href") or "").scheme in ("http", "https")
    ]
    links = [(h, t) for h, t in links if "tiuli.com" not in h and "lametayel.co.il" not in h]
    return next((h for h, t in links if t.endswith("»")), links[0][0] if links else None)


def build_events(card: Card, page: str | None, today: date, timezone: str) -> list[ScrapedEvent]:
    """The card's events; ``page`` (its detail page, if fetched) adds link, address, time, artwork."""
    starts, run_end = card_dates(card.dates, today)
    if not starts:
        raise ParseError(f"{card.url}: no dd/mm in the card's dates ({card.dates!r})")
    body = LexborHTMLParser(page).css_first("section.article-body") if page else None
    where = _WHERE.search((body.html or "") if body else "")
    # The "איפה:" line, else the place chain (its regions help the region matcher).
    where_text = html_to_text(where[1]) if where else None
    address = " ".join((where_text or ", ".join(card.places)).split()) or None
    clock = _START_TIME.search(_text(body) or "") if len(starts) == 1 and run_end is None else None
    at = time(int(clock[1]) % 24, int(clock[2])) if clock else time()
    tz = zone(timezone)
    common = {
        "source": TiuliScraper.name,
        "url": card.url,
        "title": card.title,
        "description": card.teaser,
        "city": card.places[0] if card.places else None,
        "address": address,
        "source_genres": [card.category] if card.category else [],
        "images": [i for i in [og_image(page) if page else None, card.image] if i],
    }
    extra = {"purchase_url": _purchase_url(body), "area": " | ".join(card.places[1:]) or None}
    if run_end is not None:
        # ponytail: a run (exhibition, festival week) is one event re-dated to today on every scrape,
        # so retention (by start date) keeps it until its last day; one event per day if runs need it.
        start = max(starts[0], today)
        return [
            ScrapedEvent(
                **common,
                source_event_id=card.page_id,
                start_at=datetime.combine(start, at, tz),
                end_at=datetime.combine(run_end, time(23, 59), tz),
                extra={**extra, "run_start": starts[0].isoformat()},
            )
        ]
    many = len(starts) > 1
    return [
        ScrapedEvent(
            **common,
            source_event_id=f"{card.page_id}:{day:%Y%m%d}" if many else card.page_id,
            start_at=datetime.combine(day, at, tz),
            extra=extra,
        )
        for day in starts
    ]
