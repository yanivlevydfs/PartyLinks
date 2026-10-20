"""Kupat Bravo (kupatbravo.co.il) - nationwide box office (theatre, concerts, opera, dance, kids).

Own platform (Cartissimo / katalog.co.il), no public API. Listing: the Hebrew sitemap
(``/sitemaphe.xml``) links every show page as ``/announce/<slug>``. Details: each show page
carries schema.org microdata - one ``Event`` (name, image, category in ``potentialAction``,
organizer, description) followed by flat sibling blocks per performance: ``location``
(hall, street, city, coordinates), ``offers`` (low/high price, ``offerCount`` = tickets
left, ``url`` = ``/announce/buy/<seance id>``) and ``startDate`` with its UTC offset.
The site swaps ``latitude`` and ``longitude``; they are put back by range.
One ``ScrapedEvent`` per performance; ``source_event_id`` is the seance id. robots.txt
disallows ``/announce/buy/``, so every performance links to its show page.
"""

from __future__ import annotations

import html
import re
from collections.abc import AsyncIterator
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from pydantic import Field

from partylinks.models import ScrapedEvent
from partylinks.parsing import (
    ParseError,
    clean_str,
    html_to_text,
    og_image,
    to_decimal,
    to_int,
)
from partylinks.scrapers.base import BaseScraper, SourceSettings

_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_PROP = re.compile(r'itemprop="(\w+)"\s+content="([^"]*)"')
_H1 = re.compile(r'<h1 itemprop="name">([^<]+)')
_DESCRIPTION = re.compile(r'<div itemprop="description">(.*?)</div>', re.DOTALL)
_SEANCE = re.compile(r"/buy/(\d+)")


class KupatbravoSettings(SourceSettings):
    site_url: str
    sitemap_path: str
    event_page_prefix: str  # e.g. "/announce/"
    max_events: int = Field(1500, ge=1)


class KupatbravoScraper(BaseScraper[KupatbravoSettings]):
    name = "kupatbravo"
    settings_model = KupatbravoSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        urls = sitemap_show_urls(await self.get_text(s.site_url + s.sitemap_path), s.event_page_prefix)
        if len(urls) > s.max_events:
            self.log.warning("listing truncated at max_events=%d (%d found)", s.max_events, len(urls))
        self.log.info("listed %d shows", len(urls))
        async for events in self.stream_isolated(urls[: s.max_events], self._fetch_show, label=lambda u: u):
            for event in events:
                yield event

    async def _fetch_show(self, url: str) -> list[ScrapedEvent]:
        return parse_show(await self.get_text(url), url, self.timezone)


def sitemap_show_urls(sitemap: str, prefix: str) -> list[str]:
    return list(dict.fromkeys(u for u in _LOC.findall(sitemap) if prefix in u and "/buy/" not in u))


def _props(fragment: str) -> dict[str, str]:
    """First value of every ``itemprop`` meta in the fragment (unescaped)."""
    out: dict[str, str] = {}
    for key, value in _PROP.findall(fragment):
        out.setdefault(key, html.unescape(value))
    return out


def parse_show(page_html: str, url: str, timezone: str) -> list[ScrapedEvent]:
    head, *performances = page_html.split('itemprop="location"')
    show = _props(head)
    title_match = _H1.search(head)
    title = clean_str(html.unescape(title_match.group(1))) if title_match else clean_str(show.get("performer"))
    if not title:
        raise ParseError(f"{url}: no show name")
    description = _DESCRIPTION.search(page_html)
    category = clean_str(show.get("potentialAction"))
    # og:image is sometimes a 360x248 thumbnail; the microdata image is full size.
    image = clean_str(show.get("image")) or og_image(page_html)
    common = {
        "source": KupatbravoScraper.name,
        "url": url,
        "title": title,
        "description": html_to_text(description.group(1)) if description else None,
        "source_genres": [category] if category else [],
        "organizer": clean_str(show.get("name")),
        "images": [image] if image else [],
    }
    events = []
    for block in performances:
        p = _props(block)
        seance = _SEANCE.search(p.get("url", ""))
        start = clean_str(p.get("startDate"))
        if not seance or not start:
            continue
        start_at = datetime.fromisoformat(start)
        if start_at.tzinfo is None:  # a few shows (exhibitions) give a bare local date
            start_at = start_at.replace(tzinfo=ZoneInfo(timezone))
        # Israel: latitude (~29-33) is always the smaller of the two.
        coords = sorted(v for v in (_float(p.get("latitude")), _float(p.get("longitude"))) if v)
        lat, lon = coords if len(coords) == 2 else (None, None)
        left = to_int(p.get("offerCount")) or 0
        low, high = _price(p.get("lowPrice")), _price(p.get("highPrice"))
        events.append(
            ScrapedEvent(
                **common,
                source_event_id=seance.group(1),
                start_at=start_at,
                venue_name=clean_str(p.get("name")),
                address=clean_str(p.get("streetAddress")),
                city=clean_str(p.get("addressLocality")),
                latitude=lat,
                longitude=lon,
                min_price=low if left else None,
                max_price=high if left else None,
                is_sold_out=left == 0,
                extra={"tickets_left": left, "duration": clean_str(p.get("duration"))},
            )
        )
    return events


def _price(value: str | None) -> Decimal | None:
    price = to_decimal(value)
    return price if price else None


def _float(value: str | None) -> float | None:
    try:
        return float(value) if value else None
    except ValueError:
        return None
