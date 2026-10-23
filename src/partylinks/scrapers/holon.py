"""Holon municipality's events board (holon.muni.il/Havingfun) - free and paid events across the city.

SharePoint site whose REST API (``/_api``) refuses anonymous calls (403), so the page is read.
robots.txt only disallows ``/_layouts``, ``/_vti_bin``, search and ``DispForm.aspx``. One
request: ``/Havingfun/Pages/allevents.aspx`` renders every listed event (~90, no paging) as a
``tr.rowMain`` (item id, first date and time, end, place, categories, audience) followed by a
hidden panel with title, description, phone and artwork. Libraries' story hours, lectures, youth
centres and La Park shows are on no ticket site; Holon Theatre's shows are also on its Smarticket
tenant (``hth``) and dedup merges them. Recurring series and exhibitions (pilates every week,
museum shows until 2030) give only their first date and last end, so they start in the past and
fall out by retention; a multi-day series keeps its first session's date.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import datetime
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from selectolax.lexbor import LexborHTMLParser, LexborNode

from partylinks.models import ScrapedEvent
from partylinks.parsing import ParseError, clean_str, html_to_text
from partylinks.scrapers.base import BaseScraper, SourceSettings

_DATE = re.compile(r"(\d{2}/\d{2}/\d{2,4})")
_TIME = re.compile(r"(\d{1,2}:\d{2})")
# Site categories that say nothing about the event ("general", "has a photo", the war-time label).
_NOISE_CATEGORIES = frozenset({"כללי", "תצוגת תמונות", "מלחמת חרבות ברזל"})


class HolonSettings(SourceSettings):
    listing_url: str  # https://www.holon.muni.il/Havingfun/Pages/allevents.aspx
    city: str


class HolonScraper(BaseScraper[HolonSettings]):
    name = "holon"
    settings_model = HolonSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        tree = LexborHTMLParser(await self.get_text(s.listing_url))
        rows = tree.css("tr.rowMain")
        if not rows:
            raise ParseError("no event rows on the events board")
        self.log.info("listed %d events", len(rows))
        for row in rows:
            try:
                if event := parse_row(row, tree, s, self.timezone):
                    yield event
            except (ParseError, ValueError) as exc:
                self.record_failure(f"item {row.attributes.get('itemid')}", exc)


def _text(node: LexborNode | None) -> str:
    # Separated: the date cells hide " 00:00" in a span right after the date.
    return node.text(separator=" ", strip=True) if node else ""


def _when(day: str, clock: str | None, timezone: str) -> datetime:
    """``24/10/26`` (or ``24/10/2026``) and ``11:00`` (midnight when missing) in the local zone."""
    year = "%Y" if len(day) == 10 else "%y"
    return datetime.strptime(f"{day} {clock or '00:00'}", f"%d/%m/{year} %H:%M").replace(tzinfo=ZoneInfo(timezone))


def parse_row(row: LexborNode, tree: LexborHTMLParser, s: HolonSettings, timezone: str) -> ScrapedEvent | None:
    """One board row plus its hidden detail panel (``aria-controls``)."""
    item_id = row.attributes.get("itemid")
    panel = tree.css_first(f"#{row.attributes.get('aria-controls')}")
    day = _DATE.search(_text(row.css_first("td.orgDate")))
    if not item_id or panel is None or not day:
        raise ParseError("row without id, detail panel or date")
    clock = _TIME.search(_text(row.css_first("td.orgTime")))
    start = _when(day[1], clock[1] if clock else None, timezone)
    end_text = _text(row.css_first("td.endDate"))
    end_day, end_clock = _DATE.search(end_text), _TIME.search(end_text)
    end = _when(end_day[1], end_clock[1] if end_clock else None, timezone) if end_day else None
    title = clean_str(_text(panel.css_first("h3.ttl")))
    link = panel.css_first("a.itemLink")
    if not title or link is None:
        raise ParseError(f"item {item_id}: no title or page link")

    place = row.css_first("td.placeFilter a")
    description = panel.css_first("div.txt")
    if description is not None and (heading := description.css_first("h2")):  # "תיאור"
        heading.decompose()
    image = panel.css_first("div.realImg img")
    phone = (_text(panel.css_first("div.phone")).split(":", 1) + [""])[1].strip()
    return ScrapedEvent(
        source=HolonScraper.name,
        source_event_id=item_id,
        url=urljoin(s.listing_url, link.attributes.get("href") or ""),
        title=title,
        description=html_to_text(description.html) if description is not None else None,
        start_at=start,
        # A series' end is its last session: only a same-day end is this event's.
        end_at=end if end and end.date() == start.date() and end > start else None,
        venue_name=clean_str(place.attributes.get("title") or place.text()) if place else None,
        city=s.city,
        event_types=[
            c
            for c in (p.strip() for p in _text(row.css_first("td.catFilter")).split(","))
            if c and c not in _NOISE_CATEGORIES
        ],
        images=[urljoin(s.listing_url, image.attributes["src"])]
        if image is not None and image.attributes.get("src")
        else [],
        extra={
            "audience": clean_str(_text(row.css_first("td.kahalFilter span:last-child"))),
            "phone": phone or None,
        },
    )
