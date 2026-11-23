"""IsraelShow (israelshow.co.il) - a national listings site for theatre, concerts, stand-up, dance, opera and
kids' shows, in Hebrew and Russian, selling through its own Kupat Bravo white label (israelshow.kartisim.co.il).

The site is classic ASP: one page per genre (``standup.asp``, ``classic.asp``, ``forkids.asp``...) and per
theatre / orchestra (``theater-habima.asp``...), each a list of cards: the show's name, its cities, its date
or date range ("04.12.2026-27.11.2026") and the link to ``israelshow.kartisim.co.il/announce/<id>``. The genre
pages are the site's categories (``pages``: page -> category; a theatre's page lists without one). Every
listed page is one request (~45); a show listed on several pages is read once, with every category.

The white label's robots.txt disallows everything, so the show's performances are read from the same show id
on kupatbravo.co.il (same platform, robots.txt allows ``/announce/``): its schema.org microdata gives each
performance's start, hall, street, city, coordinates, prices and tickets left (``kupatbravo.parse_show``).
One request per show, nearest first (by the card's first date; cards whose last date is past are skipped).
Each event links to the white-label page and its seance's buy page (purchase_url), which dedup keys to the
same seance as `kupatbravo`.
"""

from __future__ import annotations

import html
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date, datetime

from pydantic import Field

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.scrapers.kupatbravo import parse_show

_DATE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")


class IsraelShowSettings(SourceSettings):
    site_url: str = "https://israelshow.co.il"
    # Listing page -> the site's category for its shows ("" for a theatre's or a holiday's page).
    pages: dict[str, str] = Field(default_factory=dict)
    # The show's id in the card links (the white label's announce page).
    card_link: str = r"https?://israelshow\.kartisim\.co\.il/announce/(\d+)"
    details_url: str = "https://kupatbravo.co.il/announce/{id}"
    event_url: str = "https://israelshow.kartisim.co.il/announce/{id}"
    buy_url: str = "https://israelshow.kartisim.co.il/announce/buy/{seance}"
    max_shows: int = Field(2000, ge=1)


@dataclass
class Card:
    show_id: str
    first: date | None = None
    last: date | None = None
    categories: list[str] = field(default_factory=list)


class IsraelShowScraper(BaseScraper[IsraelShowSettings]):
    name = "israelshow"
    settings_model = IsraelShowSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        today = datetime.now(zone(self.timezone)).date()
        cards: dict[str, Card] = {}
        for page, category in s.pages.items():
            text = await self.optional(f"page {page}", self.get_text(f"{s.site_url}/{page}"))
            if text is None:
                continue
            for card in parse_cards(text, s.card_link):
                merged = cards.setdefault(card.show_id, Card(card.show_id))
                merged.first = min(filter(None, (merged.first, card.first)), default=None)
                merged.last = max(filter(None, (merged.last, card.last)), default=None)
                if category and category not in merged.categories:
                    merged.categories.append(category)
        current = [c for c in cards.values() if c.last is None or c.last >= today]
        current.sort(key=lambda c: (c.first or date.max, c.show_id))  # nearest first
        self.log.debug("listed %d shows, %d current", len(cards), len(current))
        if len(current) > s.max_shows:
            self.log.warning("listing truncated at max_shows=%d (%d found)", s.max_shows, len(current))

        async def performances(card: Card) -> list[ScrapedEvent]:
            url = s.details_url.format(id=card.show_id)
            return relabel(parse_show(await self.get_text(url), url, self.timezone), card, s)

        async for events in self.stream_isolated(current[: s.max_shows], performances, lambda c: f"show {c.show_id}"):
            for event in sorted(events, key=lambda e: e.start_at or datetime.max.replace(tzinfo=zone(self.timezone))):
                if is_current(event.start_at, event.end_at, RetentionSettings(), self.timezone):
                    yield event


def parse_cards(page_html: str, card_link: str) -> list[Card]:
    """A listing page's cards: the show id and the first / last date of each (in page order)."""
    link = re.compile(card_link)
    cards: dict[str, Card] = {}
    for item in re.split(r"<li\b", page_html)[1:]:
        found = link.search(item)
        if not found:
            continue
        span = re.search(r'<span class="dates">(.*?)</span>', item, re.DOTALL)
        days = sorted(d for d in (_day(m) for m in _DATE.finditer(html.unescape(span.group(1)) if span else "")) if d)
        card = cards.setdefault(found.group(1), Card(found.group(1)))
        if days:
            card.first = min(filter(None, (card.first, days[0])))
            card.last = max(filter(None, (card.last, days[-1])))
    return list(cards.values())


def relabel(events: list[ScrapedEvent], card: Card, s: IsraelShowSettings) -> list[ScrapedEvent]:
    """Kupat Bravo's reading of the show, as IsraelShow sells it: its pages, its categories, its links."""
    out = []
    for event in events:
        genres = list(dict.fromkeys([*(g.lower() for g in card.categories), *event.source_genres]))
        extra = {**event.extra, "purchase_url": s.buy_url.format(seance=event.source_event_id)}
        update = {
            "source": IsraelShowScraper.name,
            "url": s.event_url.format(id=card.show_id),
            "source_genres": genres,
            "extra": extra,
        }
        out.append(ScrapedEvent.model_validate({**event.model_dump(), **update}))
    return out


def _day(match: re.Match[str]) -> date | None:
    day, month, year = (int(x) for x in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None
