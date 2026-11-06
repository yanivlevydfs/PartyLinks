"""80sMusic (80smusic.co.il) - a guide to 80s / 90s / new wave / dark 80s / EBM parties in Tel Aviv.

The parties page (``/pages/parties``) is static HTML that carries each upcoming party as a
schema.org ``MusicEvent`` in its JSON-LD: name, start, venue (sometimes with street), the site's
music genres ("80s", "New Wave", "Dark 80s", "EBM"), the DJs (``performer``), the poster, and the
box office link (``offers.url``, so far always IWANT) with the price when the site states it. One
request per run; robots.txt allows everything but the admin and data paths.

The guide has no page per event, so ``url`` is the parties page and ``source_event_id`` is the box
office link's path ("PATIFON", "event.php?id=43"). IWANT's short links ("iwant.co.il/PATIFON") are
read once each for their canonical ``event.php?id=N`` URL, so the listing merges with IWANT's own.

``startDate`` carries "+03:00" in winter too (Israel is +02:00 from late October), while its clock
is the right local time (IWANT: 21:30), so the offset is dropped and the clock read in Israel time.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlparse

from pydantic import Field
from selectolax.lexbor import LexborHTMLParser

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import ParseError, clean_str, has_time, jsonld_nodes, parse_iso, schema_types, to_decimal, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings


class Music80sSettings(SourceSettings):
    site_url: str = "https://80smusic.co.il"
    parties_path: str = "/pages/parties"
    # Box office short links whose page names the canonical event URL (rel=canonical).
    canonical_hosts: list[str] = Field(default_factory=lambda: ["iwant.co.il"])


class Music80sScraper(BaseScraper[Music80sSettings]):
    name = "music80s"
    settings_model = Music80sSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        page_url = s.site_url + s.parties_path
        page = await self.get_text(page_url)
        retention = RetentionSettings()
        for item in parse_listing(page):
            try:
                event = parse_event(item, page_url, self.timezone)
            except Exception as exc:  # noqa: BLE001 - one bad item must not cost the others
                self.record_failure(f"event {item.get('name')}", exc)
                continue
            if event is None or not is_current(
                event.start_at, event.end_at, retention, self.timezone, time_known=event.time_known
            ):
                continue
            link = event.extra.get("purchase_url")
            if link and urlparse(link).hostname in s.canonical_hosts and "event.php" not in link:
                canonical = await self.optional(f"canonical {link}", self._canonical(link))
                if canonical:
                    event = event.model_copy(update={"extra": {**event.extra, "purchase_url": canonical}})
            yield event

    async def _canonical(self, url: str) -> str | None:
        node = LexborHTMLParser(await self.get_text(url)).css_first('link[rel="canonical"]')
        return clean_str(node.attributes.get("href")) if node else None


def parse_listing(page_html: str) -> list[dict[str, Any]]:
    """The page's ``MusicEvent`` / ``Event`` nodes."""
    events = [n for n in jsonld_nodes(page_html) if schema_types(n) & {"musicevent", "event"}]
    if not events:
        raise ParseError("no MusicEvent on the parties page")
    return events


def parse_event(item: dict[str, Any], page_url: str, timezone: str) -> ScrapedEvent | None:
    """One ``MusicEvent``; None when cancelled or postponed."""
    if str(item.get("eventStatus") or "").endswith(("EventCancelled", "EventPostponed")):
        return None
    title, start = clean_str(item.get("name")), parse_iso(item.get("startDate"), timezone)
    offer = item.get("offers") if isinstance(item.get("offers"), dict) else {}
    link = clean_str(offer.get("url"))
    if not title or not start or not link:
        raise ParseError("event without name, start or box office link")
    start = start.replace(tzinfo=zone(timezone))  # the clock is local; the offset is wrong in winter
    parsed = urlparse(link)
    location = item.get("location") if isinstance(item.get("location"), dict) else {}
    address = location.get("address") if isinstance(location.get("address"), dict) else {}
    city = clean_str(address.get("addressLocality"))
    venue = clean_str(location.get("name"))
    street = clean_str(address.get("streetAddress"))
    performers = item.get("performer") if isinstance(item.get("performer"), list) else []
    image = item.get("image")
    images = image if isinstance(image, list) else [image]
    price = to_decimal(offer.get("price"))
    return ScrapedEvent(
        source=Music80sScraper.name,
        source_event_id=(parsed.path.strip("/") + ("?" + parsed.query if parsed.query else "")) or link,
        url=page_url,
        title=title,
        start_at=start,
        time_known=has_time(item.get("startDate")),
        venue_name=venue,
        address=street or ", ".join(p for p in (venue, city) if p) or None,
        city=city,
        source_genres=[g.strip().lower() for g in item.get("genre") or [] if isinstance(g, str) and g.strip()],
        artists=[n for p in performers if isinstance(p, dict) and (n := clean_str(p.get("name")))],
        images=[i for i in images if isinstance(i, str) and i.startswith("http")],
        min_price=price,
        max_price=price,
        is_sold_out=str(offer.get("availability") or "").endswith("SoldOut"),
        extra={"purchase_url": link},
    )
