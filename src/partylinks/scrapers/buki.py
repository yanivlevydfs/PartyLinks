"""Buki Naeh (buki.co.il, "בוקי נאה") - the crime reporter's night tours of Tel Aviv's back yard ("סיורי לילה
בחצר האחורית של תל אביב": police-and-thieves stories, crime scenes, meetings with ex-cons).

The site is WordPress + Elementor with no events post type: the coming tour dates are a text block ("טיולי פלילים
בתל אביב – התאריכים הקרובים: יום חמישי – 24.12.26 ...") on the home page and the tours page. One wp-json call
(``/wp/v2/pages``, every page of the site, 100 a page, read to the last page) gives every page's HTML; each line
that is a weekday and a date ("יום חמישי – 24.12.26", "12.3.20 – יום חמישי") is one tour. Older blocks of
the same pages still list 2020 dates: past, dropped by ``is_current``. The London and Sicily trips abroad have
no dates on the site (sold through Natour): their pages are read, and a dated line on one is logged, not
imported as a Tel Aviv tour. The site gives no start time, meeting point or price (told by phone on sign-up):
the date is the event, without a time and without a pin (the city only). Sign-up is by phone / the contact
form: the tours page is the link.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import date, datetime, time
from html import unescape
from typing import Any

from pydantic import Field
from selectolax.lexbor import LexborHTMLParser

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_DATE = r"(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})"
_DAY = r"יום\s+(ראשון|שני|שלישי|רביעי|חמישי|שישי|שבת)"
# "יום חמישי – 24.12.26" or "24.12.26 – יום חמישי" (the site writes both).
_LINE = re.compile(rf"{_DAY}\s*[–\-]\s*{_DATE}|{_DATE}\s*[–\-]\s*{_DAY}")
_BLOCK = re.compile(r"<(?:br|/p|/h\d|/li|/div)[^>]*>", re.IGNORECASE)


class BukiSettings(SourceSettings):
    site_url: str = "https://buki.co.il"
    pages_path: str = "/wp-json/wp/v2/pages?per_page={per_page}&page={page}&_fields=id,status,link,title,content"
    per_page: int = 100  # WordPress's maximum
    max_pages: int = 20  # a runaway guard only: the site has 15 pages
    # The Tel Aviv tours page: the events' link, title and text.
    tour_page_id: int = 2405
    # The trips abroad (London, Sicily x2): no dates on the site; a dated line there is not a Tel Aviv tour.
    abroad_page_ids: list[int] = Field(default_factory=lambda: [3091, 3128, 2745])
    title: str = "סיורי לילה בחצר האחורית של תל אביב עם בוקי נאה"
    genres: list[str] = Field(default_factory=lambda: ["סיורים"])
    image_url: str | None = "https://buki.co.il/wp-content/uploads/2017/07/תל-אביב-לטלפון.jpg"
    min_text_chars: int = 60  # the tours page's first paragraph this long is the event's text
    city: str = "תל אביב"
    organizer: str = "בוקי נאה"


class BukiScraper(BaseScraper[BukiSettings]):
    name = "buki"
    settings_model = BukiSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        pages: list[Any] = []
        for n in range(1, s.max_pages + 1):  # every page, to the last (a short one)
            batch = await self.get_json(s.site_url + s.pages_path.format(per_page=s.per_page, page=n), expect=list)
            pages.extend(batch)
            if len(batch) < s.per_page:
                break
        days: set[date] = set()
        for page in pages:
            if not isinstance(page, dict) or page.get("status", "publish") != "publish":
                continue
            html = str((page.get("content") or {}).get("rendered") or "")
            try:
                found = tour_dates(html)
            except Exception as exc:  # noqa: BLE001 - one odd page never drops the others
                self.record_failure(f"page {page.get('id')}", exc)
                continue
            if found and page.get("id") in s.abroad_page_ids:
                self.log.warning("trip page %s lists dates %s: not read (no Tel Aviv tour)", page.get("id"), found)
                continue
            days.update(found)
        tour = next((p for p in pages if isinstance(p, dict) and p.get("id") == s.tour_page_id), {})
        retention = RetentionSettings()
        for day in sorted(days):  # nearest first
            event = build_event(day, tour, s, self.timezone)
            if is_current(event.start_at, event.end_at, retention, self.timezone, time_known=False):
                yield event


def tour_dates(html: str) -> list[date]:
    """The dates of the dated lines ("יום חמישי – 24.12.26") of a page's HTML, in page order, once each."""
    text = unescape(re.sub(r"<[^>]+>", " ", _BLOCK.sub("\n", html)))
    out: list[date] = []
    for m in _LINE.finditer(text):
        d, mo, y = (m.group(2), m.group(3), m.group(4)) if m.group(1) else (m.group(5), m.group(6), m.group(7))
        year = int(y) + 2000 if len(y) == 2 else int(y)
        try:
            day = date(year, int(mo), int(d))
        except ValueError:
            continue
        if day not in out:
            out.append(day)
    return out


def intro(html: str, min_chars: int) -> str | None:
    """The first text block of the tours page long enough to say what the tour is (an Elementor heading)."""
    for para in LexborHTMLParser(html).css("p, h2, h3, h4, h5"):
        text = clean_str(para.text(separator=" "))
        if text and len(text) >= min_chars and not _LINE.search(text):
            return text
    return None


def build_event(day: date, tour: dict[str, Any], s: BukiSettings, timezone: str) -> ScrapedEvent:
    """One tour date -> an event (the date only: the site gives no start time)."""
    html = str((tour.get("content") or {}).get("rendered") or "")
    url = clean_str(str(tour.get("link") or "")) or s.site_url + "/"
    return ScrapedEvent(
        source=BukiScraper.name,
        source_event_id=day.isoformat(),
        url=url,
        title=s.title,
        description=intro(html, s.min_text_chars),
        start_at=datetime.combine(day, time.min, tzinfo=zone(timezone)),
        time_known=False,
        city=s.city,
        source_genres=[g.lower() for g in s.genres],
        organizer=s.organizer,
        images=[s.image_url] if s.image_url else [],
        extra={"purchase_url": url},
    )
