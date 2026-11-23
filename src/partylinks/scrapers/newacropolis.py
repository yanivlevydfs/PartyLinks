"""New Acropolis Israel (newacropolis.org.il, "אקרופוליס החדשה") - philosophy talks, open evenings, fairs, trips.

A Wix site with Wix Events: ``/event-pages-sitemap.xml`` lists every event page (``/event-details/<slug>``,
~370, mostly past: the sitemap keeps them). Read: the pages updated within ``recent_days`` (their
``<lastmod>``; an upcoming event's page is created or edited shortly before it), every occurrence of a
recurring event whose slug ends in a coming date (``-2026-12-14-17-45``: the series' pages keep the
lastmod of its creation, Nei Kung's monthly evening was cut by the window, Nov 2026), and every page the
site's events page (``listing_paths``, its Wix Events widget: all coming events, ``hasMore`` false) links
to. Past pages are not fetched. Each page (~1.3 MB) carries a schema.org
``Event``: name, description, start and end (with offset), image, and a location whose ``address`` is one
text ("סלומון 7, תל אביב-יפו, ישראל", "ברשבסקי 10 ראשון לציון"). Its branches' streets (``branches``)
give the city and the venue when the text has no comma.

Not events: the course's own weekly sessions (``skip_titles``, exact titles; its open evenings and first
open lesson are kept) and volunteering days (``skip_title_words``). The site has no categories.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import unquote
from zoneinfo import ZoneInfo

from pydantic import Field

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, html_to_text, jsonld_nodes, parse_iso
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_ENTRY = re.compile(r"<url>\s*<loc>\s*([^<]+?)\s*</loc>\s*(?:<lastmod>\s*([^<]+?)\s*</lastmod>)?")
_COUNTRY = {"ישראל", "israel"}
_DATED = re.compile(r"-(\d{4})-(\d{2})-(\d{2})-\d{2}-\d{2}$")  # a recurring event's occurrence: its date and time
_LINK = re.compile(r"""/event-details/([^\\"'?#&<\s/]+)""")  # a link to an event page: its slug


class NewAcropolisSettings(SourceSettings):
    site_url: str = "https://www.newacropolis.org.il"
    sitemap_path: str = "/event-pages-sitemap.xml"
    recent_days: int = Field(180, ge=1)  # event pages updated within this many days are read
    listing_paths: list[str] = Field(default_factory=lambda: ["/אירועים"])  # the events page: every coming event
    # The branches: a street (as the address starts) -> (city, venue name).
    branches: dict[str, tuple[str, str]] = Field(default_factory=dict)
    skip_titles: list[str] = Field(default_factory=list)  # exact titles (the course's own sessions)


class NewAcropolisScraper(BaseScraper[NewAcropolisSettings]):
    name = "newacropolis"
    settings_model = NewAcropolisSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        xml = await self.get_text(s.site_url + s.sitemap_path)
        since = datetime.now(UTC) - timedelta(days=s.recent_days)
        urls = recent_pages(xml, since, datetime.now(ZoneInfo(self.timezone)).date())
        self.log.debug("%d event pages updated since %s or dated ahead", len(urls), since.date())
        for path in s.listing_paths:
            urls += [u for u in listed_pages(await self.get_text(s.site_url + path), s.site_url) if u not in urls]
        skip = {t.strip() for t in s.skip_titles}

        async def fetch(url: str) -> ScrapedEvent | None:
            event = parse_event(await self.get_text(url), url, s)
            return None if event is None or event.title.strip() in skip else event

        async for event in self.stream_isolated(urls, fetch, label=lambda u: u):
            if is_current(
                event.start_at, event.end_at, RetentionSettings(), self.timezone, time_known=event.time_known
            ):
                yield event


def recent_pages(sitemap_xml: str, since: datetime, today: date | None = None) -> list[str]:
    """The sitemap's event pages updated since ``since``, newest first (a page without a lastmod is kept),
    then the occurrences whose slug's date is ``today`` or later (a recurring series keeps an old lastmod)."""
    pages: list[tuple[datetime, str]] = []
    dated: list[str] = []
    for url, lastmod in _ENTRY.findall(sitemap_xml):
        if "/event-details/" not in url:
            continue
        stamp = parse_iso(lastmod, "Asia/Jerusalem") if lastmod else None
        if stamp is None or stamp >= since:
            pages.append((stamp or datetime.max.replace(tzinfo=UTC), url))
        elif today is not None and (m := _DATED.search(url.rstrip("/"))):
            try:
                if date(int(m[1]), int(m[2]), int(m[3])) >= today:
                    dated.append(url)
            except ValueError:
                continue
    return [url for _, url in sorted(pages, reverse=True)] + sorted(dated)


def listed_pages(page_html: str, site_url: str) -> list[str]:
    """The event pages a listing page (the Wix Events widget) links to, in its order, each once."""
    out: list[str] = []
    for slug in _LINK.findall(page_html):
        url = f"{site_url.rstrip('/')}/event-details/{unquote(slug)}"
        if url not in out:
            out.append(url)
    return out


def place(address: str | None, name: str | None, s: NewAcropolisSettings) -> tuple[str | None, str | None, str | None]:
    """(venue, street address, city) from the location's text and name."""
    text = clean_str(address) or ""
    branch = next((b for street, b in s.branches.items() if text.startswith(street)), None)
    parts = [p for part in text.split(",") if (p := part.strip())]
    if parts and parts[-1].lower() in _COUNTRY:
        parts = parts[:-1]
    city = branch[0] if branch else (parts[-1] if len(parts) > 1 else None)
    street = parts[0] if len(parts) > 1 else (text or None)
    named = clean_str(name)
    # The location's name is a venue when it is not just the city or the street again.
    venue = named if named and named not in (city, street) else branch[1] if branch else street
    return venue, street, city


def parse_event(page_html: str, url: str, s: NewAcropolisSettings) -> ScrapedEvent | None:
    """An event page -> its event (the JSON-LD ``Event``); None without one, a title or a start, or cancelled."""
    node = next((n for n in jsonld_nodes(page_html) if n.get("@type") == "Event"), None)
    if node is None or "Cancelled" in str(node.get("eventStatus") or ""):
        return None
    title, start_at = clean_str(node.get("name")), parse_iso(node.get("startDate"), "Asia/Jerusalem")
    if not title or start_at is None:
        return None
    location: dict[str, Any] = node.get("location") if isinstance(node.get("location"), dict) else {}
    address = location.get("address")
    if isinstance(address, dict):
        address = ", ".join(str(v) for k, v in address.items() if k in ("streetAddress", "addressLocality") and v)
    venue, street, city = place(address, location.get("name"), s)
    image = node.get("image")
    image_url = image.get("url") if isinstance(image, dict) else image if isinstance(image, str) else None
    end_at = parse_iso(node.get("endDate"), "Asia/Jerusalem")
    return ScrapedEvent(
        source=NewAcropolisScraper.name,
        source_event_id=url.rstrip("/").rsplit("/event-details/", 1)[-1],
        url=url,
        title=title,
        description=html_to_text(clean_str(node.get("description"))),
        start_at=start_at,
        end_at=end_at if end_at and end_at > start_at else None,
        venue_name=venue,
        address=street,
        city=city,
        images=[image_url] if image_url and image_url.startswith("http") else [],
    )
