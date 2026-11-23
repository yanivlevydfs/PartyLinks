"""Cluberim (cluberim.co.il, "קלאברים") - a party guide: club nights, festivals and holiday parties across Israel,
by music style, region and kind, each linking the producer's ticket page (Go-Out, Zygo, Eventer).

The site is WordPress (JetEngine). Its ``events`` post type is in wp-json: one call (``events_path``, 100 a page,
``_embed`` for the artwork and the terms) gives every listed event with JetEngine's meta: the place
(``event_adrs``: "מון צ׳ילד, נמל תל אביב", or a street, a region, a secret "ישראל"), the date as the site writes it
("26 בנובמבר, חמישי, 22:00"; a festival "26-27 במרץ 2027, שישי-שבת"), the day as a timestamp
(``event_date_sort``, the local date at 00:00 UTC) and the ticket link (``event_link``). The terms are the music
style / holiday (``event-genre``), the kind (``event-type``: "חיי לילה ומסיבות", "פסטיבלים", "הופעות"), the area
(``event-area``: "בתל אביב", "במרכז") and the line-up (``artist``). The site lists only coming events (~10).
No coordinates: the place and town are geocoded. A secret location ("ישראל") cannot be placed: not imported
(the producer's own listing, on Go-Out, carries it).
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, time, timedelta
from html import unescape
from typing import Any

from pydantic import Field

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, html_to_text, to_int, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_CLOCK = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_DAY_RANGE = re.compile(r"^\s*(\d{1,2})\s*-\s*(\d{1,2})\s")


class CluberimSettings(SourceSettings):
    site_url: str = "https://cluberim.co.il"
    events_path: str = "/wp-json/wp/v2/events"
    per_page: int = 100
    max_pages: int = 10
    # Area terms that are regions, not towns ("במרכז"); the rest name the town after its "ב".
    regions: list[str] = Field(default_factory=lambda: ["במרכז", "בצפון", "בדרום", "בשרון", "בגליל העליון"])
    # A place that is no place: the site's "secret location" ("ישראל").
    no_place: list[str] = Field(default_factory=lambda: ["ישראל"])
    # A site term -> what is declared instead (ours read some names otherwise / not at all).
    labels: dict[str, list[str]] = Field(default_factory=dict)


class CluberimScraper(BaseScraper[CluberimSettings]):
    name = "cluberim"
    settings_model = CluberimSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings

        async def page(index: int) -> tuple[list[dict[str, Any]], str | None]:
            params = {"per_page": s.per_page, "page": index + 1, "_embed": "wp:featuredmedia,wp:term"}
            response = await self.request("GET", s.site_url + s.events_path, params=params)
            rows = response.json()
            total = response.headers.get("X-WP-Total")
            return ([r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []), total

        posts = await self.paginate(page, s.per_page, s.max_pages)
        self.log.debug("listed %d events", len(posts))
        events = []
        for post in posts:
            try:
                event = parse_post(post, s, self.timezone)
            except Exception as exc:  # noqa: BLE001 - one odd post never drops the others
                self.record_failure(f"event {post.get('id')}", exc)
                continue
            if event and is_current(
                event.start_at, event.end_at, RetentionSettings(), self.timezone, time_known=event.time_known
            ):
                events.append(event)
        for event in sorted(events, key=lambda e: (e.start_at, e.source_event_id)):
            yield event  # nearest first


def parse_post(post: dict[str, Any], s: CluberimSettings, timezone: str) -> ScrapedEvent | None:
    """One ``events`` post -> its event; None without an id, a link, a title or a day."""
    meta = post.get("meta") if isinstance(post.get("meta"), dict) else {}
    title = clean_str(unescape(str((post.get("title") or {}).get("rendered") or "")))
    stamp = to_int(meta.get("event_date_sort"))
    if not post.get("id") or not post.get("link") or not title or not stamp:
        return None
    tz = zone(timezone)
    day = datetime.fromtimestamp(stamp, UTC).date()  # the local date, stored at 00:00 UTC
    when = clean_str(meta.get("event_dates")) or ""
    clock = _CLOCK.search(when)
    start = datetime.combine(day, time(int(clock.group(1)), int(clock.group(2))) if clock else time.min, tzinfo=tz)
    end = None
    if (span := _DAY_RANGE.match(when)) and int(span.group(2)) > int(span.group(1)):  # "26-27 במרץ": two days
        end = datetime.combine(day + timedelta(days=int(span.group(2)) - int(span.group(1)) + 1), time.min, tzinfo=tz)
    terms = _terms(post)
    areas = [a for a in terms.get("event-area", []) if a not in s.regions]
    place = clean_str(meta.get("event_adrs"))
    if not place or place in s.no_place:
        return None  # a secret location ("ישראל"): no place to put it (its seller's listing has it)
    parts = [p.strip() for p in (place or "").split(",") if p.strip()]
    venue = parts[0] if len(parts) > 1 and not any(c.isdigit() for c in parts[0]) else None
    town = next((a.removeprefix("ב") for a in areas), None)
    city = town or (parts[-1] if len(parts) > 1 else place)  # the area's town ("בתל אביב"), else the address's
    genres: list[str] = []
    for name in [*terms.get("event-genre", []), *terms.get("event-type", [])]:
        genres += s.labels.get(name, [name])
    extra: dict[str, Any] = {}
    link = clean_str(meta.get("event_link"))
    if link and link.startswith(("http://", "https://")):
        extra["purchase_url"] = link
    media = (post.get("_embedded") or {}).get("wp:featuredmedia") or []
    images = [
        m["source_url"] for m in media if isinstance(m, dict) and str(m.get("source_url") or "").startswith("http")
    ]
    content = str((post.get("content") or {}).get("rendered") or "")
    return ScrapedEvent(
        source=CluberimScraper.name,
        source_event_id=str(post["id"]),
        url=str(post["link"]),
        title=title,
        description=html_to_text(content) if content else None,
        start_at=start,
        end_at=end,
        time_known=bool(clock),
        venue_name=venue,
        address=place,
        city=city,
        source_genres=list(dict.fromkeys(g.lower() for g in genres)),
        artists=terms.get("artist", []),
        images=images,
        extra=extra,
    )


def _terms(post: dict[str, Any]) -> dict[str, list[str]]:
    """The embedded terms by taxonomy, names cleaned (the site writes "חיי לילה ומסיבות" with no-break spaces)."""
    out: dict[str, list[str]] = {}
    for group in (post.get("_embedded") or {}).get("wp:term") or []:
        for term in group if isinstance(group, list) else []:
            if isinstance(term, dict) and (
                name := clean_str(unescape(str(term.get("name") or "")).replace("\xa0", " "))
            ):
                out.setdefault(str(term.get("taxonomy")), []).append(name)
    return out
