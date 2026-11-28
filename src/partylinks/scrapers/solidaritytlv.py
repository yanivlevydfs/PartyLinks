"""Solidarity (solidaritytlv.org) - the Solidarity Festival for Film and Human Rights, Tel Aviv
Cinematheque (2026: 10-19.12). A Wix site: its own pages, the events the Cinematheque's box office
does not sell.

Every screening on the site (films, timetable, special screenings, Anat Even tribute, the 47 film pages)
links a Cinematheque PRES order: those are read by ``cinematheque_tlv`` (27.11.2026: all 33 order ids
on the site are in its feed), so this source does not repeat them. Read here:

* ``industry-events``: a Wix repeater, one item per panel / meeting (``comp-…__<item id>``): the title is
  the item picture's ``alt``, the date line ``13.12.26 | 13:30`` with the hall under it, the longest
  text its description. Items the page hides (the repeater's template rows) are skipped.
* ``conference``: one day of talks, "יום א׳, 13.12, 21:30-15:30 אולם 6" (the hours as the right-to-left
  page shows them: from the earlier to the later), free entry with registration.

The Galilee screenings (``solidarity-galilee``) are sold on Eventati and read by ``eventati``. Not read:
about, FAQ, support-us, search, the English mirror (solidaritytlveng.org: the same events).
"""

from __future__ import annotations

import html as html_lib
import re
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta

from pydantic import BaseModel, Field

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_ITEM_DATE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})\s*\|\s*(\d{1,2}):(\d{2})(.*)", re.DOTALL)
_DAY_HOURS = re.compile(r"(\d{1,2})\.(\d{1,2})\D{0,12}?(\d{1,2}):(\d{2})\s*[-–]\s*(\d{1,2}):(\d{2})")
_HALL = re.compile(r"(אולם\s*\d+)")
_COMP = re.compile(r'<div id="comp-([a-z0-9]+)__([0-9a-f-]{36})"[^>]*>(.*?)</div>', re.DOTALL)
_HIDDEN = re.compile(r'"comp-([a-z0-9]+)__([0-9a-f-]{36})":\{(?:"collapsed":(?:true|false),)?"hidden":true')
_IMG = re.compile(r'<img src="(https://static\.wixstatic\.com/media/[^"/]+)[^"]*"[^>]*alt="([^"]+)"')
_TAG = re.compile(r"<[^>]+>")
_OG_IMAGE = re.compile(r'<meta property="og:image" content="(https://static\.wixstatic\.com/media/[^"/]+)')
_DESCRIPTION = re.compile(r'<meta name="description" content="([^"]+)"')


class SolidarityPage(BaseModel):
    path: str
    kind: str  # "repeater" (one event per item) or "day" (the page is one event)
    title: str | None = None  # a "day" page's title
    free: bool = False


class SolidaritySettings(SourceSettings):
    site_url: str = "https://www.solidaritytlv.org"
    venue_name: str = "סינמטק תל אביב"
    address: str = "הארבעה 5, תל אביב-יפו"
    city: str = "תל אביב-יפו"
    # The Cinematheque's pin as its own listings have it (cinematheque_tlv, 27.11.2026).
    latitude: float = 32.0707072
    longitude: float = 34.7834524
    pages: list[SolidarityPage] = Field(default_factory=list)
    free_label: str = "כניסה חופשית בהרשמה מראש"  # the tier of a free day with registration


class SolidarityScraper(BaseScraper[SolidaritySettings]):
    name = "solidaritytlv"
    settings_model = SolidaritySettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        today = datetime.now(zone(self.timezone)).date()
        retention = RetentionSettings()
        for page in s.pages:
            url = s.site_url + page.path
            text = await self.optional(f"page {page.path}", self.get_text(url))
            if not text:
                continue
            events = (
                repeater_events(text, url, s, self.timezone)
                if page.kind == "repeater"
                else day_event(text, url, page, s, self.timezone, today)
            )
            for event in events:
                if is_current(event.start_at, event.end_at, retention, self.timezone):
                    yield event


def _plain(fragment: str) -> str:
    return " ".join(html_lib.unescape(_TAG.sub(" ", fragment.replace("<br", "\n<br"))).split())


def _base(url: str, s: SolidaritySettings, **fields: object) -> ScrapedEvent:
    defaults: dict[str, object] = {
        "venue_name": s.venue_name,
        "address": s.address,
        "city": s.city,
        "latitude": s.latitude,
        "longitude": s.longitude,
    }
    return ScrapedEvent.model_validate(
        {"source": "solidaritytlv", "url": url, "source_genres": ["פסטיבל סולידריות"], "extra": {"purchase_url": url}}
        | defaults
        | fields
    )


def repeater_events(page_html: str, url: str, s: SolidaritySettings, timezone: str) -> list[ScrapedEvent]:
    """One event per visible repeater item that has a date line."""
    tz = zone(timezone)
    hidden = {(comp, item) for comp, item in _HIDDEN.findall(page_html)}
    texts: dict[str, list[str]] = {}
    for m in _COMP.finditer(page_html):
        comp, item, inner = m.groups()
        if (comp, item) in hidden or 'data-testid="richTextElement"' not in m.group(0):
            continue
        if text := _plain(inner):
            texts.setdefault(item, []).append(text)
    pictures = {}
    for m in re.finditer(r'id="img_comp-[a-z0-9]+__([0-9a-f-]{36})"', page_html):
        found = _IMG.search(page_html, m.end(), m.end() + 3000)
        if found:
            pictures.setdefault(m.group(1), found.groups())
    events = []
    for item, lines in texts.items():
        dated = next((d for line in lines if (d := _ITEM_DATE.search(line))), None)
        picture = pictures.get(item)
        if not dated or not picture:
            continue
        day, month, year, hour, minute, rest = dated.groups()
        year_n = int(year) + 2000 if len(year) == 2 else int(year)
        hall = _HALL.search(rest)
        description = max((line for line in lines if line != dated.group(0)), key=len, default=None)
        image, title = picture
        events.append(
            _base(
                f"{url}#{item}",
                s,
                source_event_id=item,
                title=html_lib.unescape(title),
                description=_with_hall(description, hall.group(1) if hall else None),
                start_at=datetime(year_n, int(month), int(day), int(hour), int(minute), tzinfo=tz),
                images=[image],
            )
        )
    return events


def _with_hall(description: str | None, hall: str | None) -> str | None:
    """The hall ("אולם 6") as the description's first line: the venue stays the Cinematheque's own name."""
    return "\n\n".join(p for p in (hall, description) if p) or None


def _year(day: int, month: int, today: date) -> int:
    """The nearest year in which day.month is not past (yesterday still counts: late events)."""
    candidate = date(today.year, month, day)
    return today.year + 1 if candidate < today - timedelta(days=1) else today.year


def day_event(
    page_html: str, url: str, page: SolidarityPage, s: SolidaritySettings, timezone: str, today: date
) -> list[ScrapedEvent]:
    """A page that is one event: its day and hours ("13.12, 21:30-15:30"), title from config."""
    text = _plain(page_html[page_html.find("<body") :])
    m = _DAY_HOURS.search(text)
    if not m:
        return []
    day, month = int(m.group(1)), int(m.group(2))
    tz = zone(timezone)
    year = _year(day, month, today)
    times = sorted([(int(m.group(3)), int(m.group(4))), (int(m.group(5)), int(m.group(6)))])
    start = datetime(year, month, day, *times[0], tzinfo=tz)
    end = datetime(year, month, day, *times[1], tzinfo=tz)
    hall = _HALL.search(text[m.end() : m.end() + 40])
    description = _DESCRIPTION.search(page_html)
    image = _OG_IMAGE.search(page_html)
    return [
        _base(
            url,
            s,
            source_event_id=page.path.strip("/"),
            title=page.title or "פסטיבל סולידריות",
            description=_with_hall(
                html_lib.unescape(description.group(1)) if description else None, hall.group(1) if hall else None
            ),
            start_at=start,
            end_at=end,
            images=[image.group(1)] if image else [],
            tickets=[TicketTier(name=s.free_label, price=0, is_free=True)] if page.free else [],
            min_price=0 if page.free else None,
            max_price=0 if page.free else None,
        )
    ]
