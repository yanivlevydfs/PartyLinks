"""Reading 3 (reading3.co.il) - concert hall at the Tel Aviv port (Yosef Yekutieli 6).

An old ASP.NET site with no API, JSON-LD or robots.txt. The shows page (``/he/shows/a/main/``) lists
every show on sale as an artwork card linking to ``/he/shows/a/view/?ContentID=<id>``. Each show's page
has a table with one row per performance (date, door time, start, standing / seated, "החל מ-124 ש"ח"
and the seller's link: Eventer, Kartisim, Vibez, Kupat...), the title and the text. One request for the
listing plus one per show (~10). One venue: its place comes from the config.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import datetime
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

from selectolax.lexbor import LexborHTMLParser

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, html_to_text, to_decimal, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

_DATE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")
_CLOCK = re.compile(r"(\d{1,2}):(\d{2})")
_IMAGE = re.compile(r"url\('([^']+)'\)")


class Reading3Settings(SourceSettings):
    site_url: str
    listing_path: str = "/he/shows/a/main/"
    show_path: str = "/he/shows/a/view/?ContentID={id}"
    venue_name: str
    address: str
    city: str
    latitude: float | None = None
    longitude: float | None = None


class Reading3Scraper(BaseScraper[Reading3Settings]):
    name = "reading3"
    settings_model = Reading3Settings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        cards = parse_listing(await self.get_text(s.site_url + s.listing_path), s.site_url)
        self.log.info("listed %d shows", len(cards))
        for show_id, image in cards.items():
            url = s.site_url + s.show_path.format(id=show_id)
            page = await self.optional(f"show {show_id}", self.get_text(url))
            if not page:
                continue
            try:
                events = parse_show(page, show_id, url, image, s, self.timezone)
            except Exception as exc:  # noqa: BLE001 - one odd page never drops the others
                self.record_failure(f"show {show_id}", exc)
                continue
            for event in events:
                if is_current(
                    event.start_at, event.end_at, RetentionSettings(), self.timezone, time_known=event.time_known
                ):
                    yield event


def parse_listing(page_html: str, site_url: str) -> dict[str, str]:
    """Shows page -> {ContentID: artwork URL} (each show once, as listed)."""
    shows: dict[str, str] = {}
    for link in LexborHTMLParser(page_html).css("a[href*='ContentID=']"):
        ids = parse_qs(urlsplit(link.attributes.get("href") or "").query).get("ContentID")
        if not ids or not ids[0].isdigit():
            continue
        image = _IMAGE.search(link.attributes.get("style") or "")
        if image or ids[0] not in shows:
            shows[ids[0]] = urljoin(site_url, image.group(1)) if image else shows.get(ids[0], "")
    return shows


def _at(day: str, clock: str, timezone: str) -> datetime | None:
    d, t = _DATE.search(day), _CLOCK.search(clock)
    if d is None:
        return None
    hour, minute = (int(t.group(1)), int(t.group(2))) if t else (0, 0)
    return datetime(int(d.group(3)), int(d.group(2)), int(d.group(1)), hour, minute, tzinfo=zone(timezone))


def _seller(href: str) -> str | None:
    """The seller's link without its analytics query (Kartisim's "?_gl=..."); None for no link."""
    if not href.startswith("http"):
        return None
    parts = urlsplit(href)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "" if "_gl=" in parts.query else parts.query, ""))


def parse_show(
    page_html: str, show_id: str, url: str, image: str, s: Reading3Settings, timezone: str
) -> list[ScrapedEvent]:
    """A show's page -> one event per row of its performance table (rows without a date are skipped)."""
    tree = LexborHTMLParser(page_html)
    heading = tree.css_first(".EventTitle h1") or tree.css_first(".infoWrapper h3")
    title = clean_str(heading.text()) if heading else None
    if not title:
        return []
    info = tree.css_first(".infoWrapper")
    description = html_to_text(info.html) if info else None
    if description and description.startswith(title):
        description = description[len(title) :].strip() or None
    banner_node = tree.css_first(".EventBanner")
    banner = _IMAGE.search((banner_node.attributes.get("style") or "") if banner_node else "")
    images = [u for u in (urljoin(s.site_url, banner.group(1)) if banner else "", image) if u.startswith("http")]

    events: list[ScrapedEvent] = []
    rows = [r for r in tree.css("#EventDetailsTable tr") if r.css("td")]
    for row in rows:
        cells = [clean_str(td.text()) or "" for td in row.css("td")]
        cells += [""] * (5 - len(cells))
        day, doors, start, seating, price_text = cells[:5]
        start_at = _at(day, start or doors, timezone)
        if start_at is None:
            continue
        link = row.css_first("a[href]")
        seller = _seller(link.attributes.get("href") or "") if link else None
        price = to_decimal(re.sub(r"[^\d.]", "", price_text)) if re.search(r"\d", price_text) else None
        events.append(
            ScrapedEvent(
                source=Reading3Scraper.name,
                source_event_id=f"{show_id}-{start_at:%Y%m%d%H%M}" if len(rows) > 1 else show_id,
                url=url,
                title=title,
                description=description,
                start_at=start_at,
                time_known=bool(_CLOCK.search(start or doors)),
                venue_name=s.venue_name,
                address=s.address,
                city=s.city,
                latitude=s.latitude,
                longitude=s.longitude,
                source_genres=["הופעה"],
                images=list(dict.fromkeys(images)),
                min_price=price,
                extra={
                    "open_doors": doors or None,
                    "seating": seating or None,
                    **({"purchase_url": seller} if seller else {}),
                },
            )
        )
    return events
