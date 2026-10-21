"""Geektime (geektime.co.il/event/) - the hi-tech events calendar: meetups, conferences, workshops.

WordPress page with no events API: one ``GET /event/`` lists every upcoming event as an
``<article id="accordion-<id>">`` card with title, address, day + Hebrew month (no year),
Hebrew weekday letter and "HH:MM - HH:MM", categories ("{AI}", "{סטארטאפ ויזמות}"), type
(offline / online / hybrid), the description and a registration link (often Luma or Eventbrite,
so cross-source duplicates are expected). The year is the one, of this year and next, whose date
falls on the card's weekday. Online-only events are skipped. Anyone can submit an event, so
titles with a configured spam word are skipped.
"""

from __future__ import annotations

import html
import logging
import re
from collections.abc import AsyncIterator
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from pydantic import Field, ValidationError

from partylinks.models import ScrapedEvent
from partylinks.parsing import ParseError, clean_str, html_to_text
from partylinks.scrapers.base import BaseScraper, SourceSettings

_MONTHS = {
    m: i
    for i, m in enumerate(
        ["ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני", "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר"], 1
    )
}
_WEEKDAYS = {"א": 6, "ב": 0, "ג": 1, "ד": 2, "ה": 3, "ו": 4, "ש": 5}  # Hebrew letter -> date.weekday()
log = logging.getLogger(__name__)
_CARD = re.compile(r'<article id="accordion-(\d+)"')
_TIMES = re.compile(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})")


class GeektimeSettings(SourceSettings):
    page_url: str
    skip_title_words: list[str] = Field(default_factory=list)


class GeektimeScraper(BaseScraper[GeektimeSettings]):
    name = "geektime"
    settings_model = GeektimeSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        events = parse_page(
            await self.get_text(s.page_url),
            s.page_url,
            self.timezone,
            datetime.now(ZoneInfo(self.timezone)).date(),
            s.skip_title_words,
        )
        self.log.info("listed %d in-person events", len(events))
        for event in events:
            yield event


def parse_page(page_html: str, page_url: str, timezone: str, today: date, skip_words: list[str]) -> list[ScrapedEvent]:
    """Every in-person card on the page, once per event id."""
    parts = _CARD.split(page_html)
    cards = dict(zip(parts[1::2], parts[2::2], strict=True))
    if not cards:
        raise ParseError(f"{page_url}: no event cards")
    events = []
    for event_id, card in cards.items():
        try:
            event = parse_card(event_id, card, page_url, timezone, today)
        except (ParseError, ValidationError) as exc:  # one bad submission must not hide the rest
            log.warning("geektime card %s skipped: %s", event_id, " ".join(str(exc).split())[:200])
            continue
        if event and not any(w.casefold() in event.title.casefold() for w in skip_words):
            events.append(event)
    return events


def parse_card(event_id: str, card: str, page_url: str, timezone: str, today: date) -> ScrapedEvent | None:
    def first(pattern: str) -> str | None:
        found = re.search(pattern, card, re.DOTALL)
        return clean_str(html.unescape(re.sub(r"<[^>]+>", " ", found[1]))) if found else None

    if (first(r"class='type (\w+)'") or "offline") == "online":
        return None
    title = first(r"card__title[^>]*>(.*?)</h3>")
    day, month = (
        first(r'class="d-block day">(.*?)</div>'),
        _MONTHS.get(first(r'class="d-block month">(.*?)</div>') or ""),
    )
    when = first(r"card__range[^>]*>(.*?)</div>") or ""
    if not title or not day or not day.isdigit() or not month:
        raise ParseError(f"geektime {event_id}: card without title or date")
    on = event_day(int(day), month, _WEEKDAYS.get(when[:1]), today)
    zone, times = ZoneInfo(timezone), _TIMES.search(when)
    start = datetime.combine(on, time(), zone)
    end = None
    if times and times[0] != "00:00 - 00:00":
        start = datetime.combine(on, time(int(times[1]), int(times[2])), zone)
        end = datetime.combine(on, time(int(times[3]), int(times[4])), zone)
        end += timedelta(days=1) if end <= start else timedelta()  # "10:00 - 00:45" ends after midnight
    body = re.search(r'card__excerpt.*?<div class="w-100 d-block entry">(.*?)</div>', card, re.DOTALL)
    images = re.findall(r'<img src="([^"]+)"', body[1]) if body else []
    address = first(r"card__address[^>]*>(.*?)</div>")
    link = first(r'href="([^"]+)" class="btn btn-green')
    link = f"https://{link}" if link and not link.startswith(("http://", "https://")) else link
    return ScrapedEvent(
        source=GeektimeScraper.name,
        source_event_id=event_id,
        url=link or f"{page_url}?accordion-{event_id}",
        title=title,
        description=html_to_text(body[1]) if body else None,
        start_at=start,
        end_at=end,
        address=address,
        source_genres=["hi-tech"],
        event_types=list(dict.fromkeys(c.strip("{}") for c in re.findall(r'<div class="cat">([^<]*)', card))),
        images=images,
    )


def event_day(day: int, month: int, weekday: int | None, today: date) -> date:
    """The card's date this year or next: the one on its weekday, else the next one not past."""
    candidates = [d for year in (today.year - 1, today.year, today.year + 1) if (d := _date(year, month, day))]
    if weekday is not None and (
        match := [d for d in candidates if d.weekday() == weekday and d >= today - timedelta(days=7)]
    ):
        return match[0]
    return next(d for d in candidates if d >= today - timedelta(days=1))


def _date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None
