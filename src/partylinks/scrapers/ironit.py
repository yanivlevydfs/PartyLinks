"""Ashdod's municipal culture and leisure company (ironit.org.il, "החברה העירונית לתרבות הפנאי אשדוד").

The city's own events site (ashdod.muni.il) stopped listing events in 2023; this is where Ashdod's
community centres, Monart, the museums, Beit Kleng, the Bermuza theatre and the library publish
theirs. WordPress, robots.txt allows everything, plain requests work from anywhere.

- **Listing:** the calendar page's FullCalendar feed, one ``POST /wp-admin/admin-ajax.php?action=
  get_events`` (``start`` / ``end`` form fields): ``{title, start: "YYYYMMDD", url}`` per day of every
  "Event" post (the "Group" posts - courses and chugim - are not in it). A run (an exhibition, a
  monthly club) has a row for each of its days; it is kept once, at its next date, so it rolls
  forward instead of starting in the past. Past rows are dropped before anything else is fetched.
- **Categories:** the REST API's ``events`` post type has no dates or places (``acf`` is empty) but
  carries the taxonomy ids: ``/wp-json/wp/v2/events?pastime=<event term>`` (two pages of 100) maps
  each link to its ``events_type`` (מופעים / הרצאות / סדנאות / תערוכות וגלריות ...) and
  ``related_types`` (the institution or community centre); the two term lists name them.
- **Each event page** (``section.event-single``): the title (``<h1>``, a ``<br>`` before the
  subtitle), the place (``.location``), the date or run ("30.11.26 - 03.12.26") and the hour (empty
  for museums' runs), the ages, the description, the artwork and the "לרכישה" button.

Most events (~90% in Nov 2026) are sold through the city's Smarticket box office
(``ashdod.smarticket.co.il``), which the smarticket source reads in full: they are skipped here, so
this source adds the free events - Hanukkah parades, community evenings, exhibitions, museum tours.
The site gives no prices and no street addresses: the place name with the city is geocoded.

Requests per run: 1 calendar feed + 2 REST pages + 2 term lists + one page per upcoming event (~120).
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import date, datetime, time, timedelta
from typing import Any
from urllib.parse import unquote, urlsplit

from pydantic import Field, HttpUrl
from selectolax.lexbor import LexborHTMLParser

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import ParseError, clean_str, html_to_text, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_DATE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})")
_CLOCK = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_POST_ID = re.compile(r"\bpostid-(\d+)\b")
_BR = re.compile(r"\s*<br\s*/?>\s*", re.IGNORECASE)


class IronitSettings(SourceSettings):
    site_url: str = "https://www.ironit.org.il"
    city: str = "אשדוד"
    feed_path: str = "/wp-admin/admin-ajax.php?action=get_events"
    feed_months: int = Field(12, ge=1)
    events_rest_path: str = "/wp-json/wp/v2/events"
    # The "pastime" term of one-off events (the other one, "Group", is courses and chugim).
    event_pastime_id: int = 39
    term_paths: dict[str, str] = Field(
        default_factory=lambda: {
            "events_type": "/wp-json/wp/v2/events_type",
            "related_types": "/wp-json/wp/v2/related_types",
        }
    )
    # Umbrella terms that say nothing once a narrower one is present ("מופעים, הרצאות וסדנאות").
    umbrella_types: list[str] = Field(default_factory=lambda: ["מופעים, הרצאות וסדנאות", "Community centers"])
    # Courses filed as events ("חוגים וסדנאות", camps): not one-off events.
    skip_types: list[str] = Field(default_factory=lambda: ["חוגים", "קייטנ", "צהרונים", "מעונות"])
    skip_title_words: list[str] = Field(default_factory=lambda: ["חוג ", "קורס ", "הרשמה לחוגים"])
    # Box offices another source reads in full (the city's Smarticket tenant): their events are skipped.
    skip_purchase_hosts: list[str] = Field(default_factory=lambda: ["ashdod.smarticket.co.il"])


class IronitScraper(BaseScraper[IronitSettings]):
    name = "ironit"
    settings_model = IronitSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        today = datetime.now(zone(self.timezone)).date()
        end = today + timedelta(days=31 * s.feed_months)
        rows = await self.post_json(
            s.site_url + s.feed_path, expect=list, data={"start": today.isoformat(), "end": end.isoformat()}
        )
        dates = next_dates(rows, today)
        self.log.debug("%d upcoming event pages in the calendar feed", len(dates))
        terms = await self.optional("taxonomies", self._terms()) or {}

        async def fetch(item: tuple[str, date]) -> ScrapedEvent | None:
            url, day = item
            page = await self.get_text(url)
            return parse_event(page, url, day, terms.get(_key(url), []), s, self.timezone)

        retention = RetentionSettings.model_validate({})
        async for event in self.stream_isolated(dates.items(), fetch, label=lambda i: i[0]):
            if is_current(event.start_at, event.end_at, retention, self.timezone, time_known=event.time_known):
                yield event

    async def _terms(self) -> dict[str, list[str]]:
        """Event link (decoded) -> its type and institution names, from the REST API."""
        s = self.settings
        names: dict[int, str] = {}
        for path in s.term_paths.values():
            for term in await self.get_json(f"{s.site_url}{path}?per_page=100&_fields=id,name", expect=list):
                names[int(term["id"])] = html_to_text(str(term.get("name") or "")) or ""
        fields = ",".join(["link", *s.term_paths])
        out: dict[str, list[str]] = {}
        page = 1
        while True:
            url = f"{s.site_url}{s.events_rest_path}?pastime={s.event_pastime_id}&per_page=100&page={page}&_fields={fields}"
            posts = await self.get_json(url, expect=list)
            for post in posts:
                ids = [i for tax in s.term_paths for i in post.get(tax) or []]
                out[_key(str(post.get("link") or ""))] = [names[i] for i in ids if names.get(i)]
            if len(posts) < 100:
                return out
            page += 1


def _key(url: str) -> str:
    return unquote(url).rstrip("/").lower()


def next_dates(rows: list[dict[str, Any]], today: date) -> dict[str, date]:
    """Event page -> its next day in the feed (today or later); pages with no such day are left out."""
    out: dict[str, date] = {}
    for row in rows:
        url, start = clean_str(row.get("url")), clean_str(row.get("start"))
        if not url or not start or not re.fullmatch(r"\d{8}", start):
            continue
        day = date(int(start[:4]), int(start[4:6]), int(start[6:]))
        if day >= today and (url not in out or day < out[url]):
            out[url] = day
    return out


def _page_dates(text: str) -> list[date]:
    out = []
    for d, m, y in _DATE.findall(text):
        year = int(y) + 2000 if len(y) == 2 else int(y)
        out.append(date(year, int(m), int(d)))
    return out


def parse_event(
    html: str, url: str, day: date, terms: list[str], s: IronitSettings, timezone: str
) -> ScrapedEvent | None:
    """One event page, at ``day`` (its next date in the feed). None for a course or a box-office copy."""
    tree = LexborHTMLParser(html)
    section = tree.css_first("section.event-single")
    if section is None:
        raise ParseError(f"{url}: no event section")
    h1 = section.css_first("h1")
    title = clean_str(html_to_text(_BR.sub(" - ", h1.inner_html or ""))) if h1 else None
    if not title:
        raise ParseError(f"{url}: no title")
    if any(w in title + " " for w in s.skip_title_words):
        return None
    if any(skip in t for t in terms for skip in s.skip_types):
        return None
    buy = [clean_str(a.attributes.get("href")) for a in section.css(".button a")]
    buy = [b for b in buy if b and b.startswith("http")]
    if any(urlsplit(b).netloc.lower() in s.skip_purchase_hosts for b in buy):
        return None

    when = section.css_first(".date-time")
    date_node = when.css_first(".date") if when else None
    clock_node = when.css_first(".time") if when else None
    date_text = clean_str(date_node.text()) if date_node else ""
    clock_text = clean_str(clock_node.text()) if clock_node else ""
    days = _page_dates(date_text or "")
    clock = _CLOCK.search(clock_text or "")
    tz = zone(timezone)
    start = datetime.combine(day, time(int(clock.group(1)), int(clock.group(2))) if clock else time.min, tzinfo=tz)

    place = section.css_first(".date-time-location .location")
    venue = clean_str(place.text()) if place else None
    body = tree.css_first("body")
    post_id = _POST_ID.search(body.attributes.get("class") or "") if body else None

    images = []
    for img in section.css(".meadia-block img"):
        src = img.attributes.get("data-lazy-src") or img.attributes.get("src") or ""
        if src.startswith("http"):
            images.append(src)
    text = section.css_first(".text-container")
    ages = [clean_str(a.text()) for a in section.css(".ages a")]
    umbrella = set(s.umbrella_types)
    narrow = [t for t in terms if t not in umbrella]
    genres = list(dict.fromkeys(t.lower() for t in (narrow or terms)))

    extra: dict[str, Any] = {"ages": [a for a in ages if a]}
    if buy:
        extra["purchase_url"] = buy[0]
    if len(days) > 1 and days[-1] > days[0]:
        extra["run"] = [days[0].isoformat(), days[-1].isoformat()]
    return ScrapedEvent(
        source=IronitScraper.name,
        source_event_id=post_id.group(1) if post_id else _key(url).rsplit("/", 1)[-1],
        url=HttpUrl(url),
        title=title,
        start_at=start,
        time_known=bool(clock),
        venue_name=venue,
        city=s.city,
        source_genres=genres,
        images=list(dict.fromkeys(images)),
        description=text.html if text else None,
        extra=extra,
    )
