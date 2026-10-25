"""Airdrop (airdrop.co.il) - party guide: Israeli clubs and festivals, and party trips abroad.

robots.txt allows everything but ``/admin/``. Listing: ``/events?when=upcoming`` renders every
upcoming event as a card (``a.wcard``, ~50) linking to its page: ``/<slug>`` for festivals and
trips, ``/<club>/<slug>`` for club nights. (The sitemap is mostly past events, ~600 pages.)
Details: each page's schema.org ``MusicEvent`` JSON-LD gives name, start/end with offset, place
with a Hebrew city and country, images, genres, performers and one ``Offer`` /
``AggregateOffer`` with the price and the seller's link (Go-Out, Eventer, a travel agency),
kept as ``extra["purchase_url"]`` without Airdrop's ``?ref=`` so dedup ties it to that seller's
own listing. Trips abroad are priced as packages (flight and hotel included).
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from partylinks.artists import ArtistProfile
from partylinks.models import ScrapedEvent, TicketTier, TierStatus
from partylinks.parsing import ParseError, clean_str, html_to_text, jsonld_nodes, parse_iso, schema_types, to_decimal
from partylinks.parsing import zone as _zone
from partylinks.scrapers.abroad import country_code, local_zone
from partylinks.scrapers.base import BaseScraper, SourceSettings

_CARD = re.compile(r'<a\s+class="wcard\b[^"]*"\s+href="([^"#]+)"')


class AirdropSettings(SourceSettings):
    site_url: str
    listing_path: str


class AirdropScraper(BaseScraper[AirdropSettings]):
    name = "airdrop"
    settings_model = AirdropSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        urls = listing_urls(await self.get_text(s.site_url + s.listing_path), s.site_url)
        if not urls:
            raise ParseError("listing has no event cards")
        self.log.info("listed %d upcoming events", len(urls))
        async for event in self.stream_isolated(urls, self._fetch_event, label=lambda u: u):
            yield event

    async def _fetch_event(self, url: str) -> ScrapedEvent | None:
        return parse_event(await self.get_text(url), url, self.timezone)


def listing_urls(page_html: str, site_url: str) -> list[str]:
    """Event pages the listing's cards link to, in order, once each."""
    return list(dict.fromkeys(urljoin(site_url + "/", href) for href in _CARD.findall(page_html)))


def parse_event(page_html: str, url: str, timezone: str) -> ScrapedEvent | None:
    """The page's ``MusicEvent``; None when cancelled."""
    event = next((n for n in jsonld_nodes(page_html) if schema_types(n) & {"event", "musicevent", "festival"}), None)
    if event is None:
        raise ParseError(f"{url}: no Event JSON-LD")
    if str(event.get("eventStatus") or "").endswith("EventCancelled"):
        return None
    location = event.get("location") if isinstance(event.get("location"), dict) else {}
    address = location.get("address") if isinstance(location.get("address"), dict) else {}
    country = country_code(clean_str(address.get("addressCountry")) or "ישראל")
    zone = local_zone(country, timezone) if country != "IL" else timezone
    title, start = clean_str(event.get("name")), _local(event.get("startDate"), zone, country)
    if not title or not start:
        raise ParseError(f"{url}: Event without name or start")
    offer = next((o for o in _list(event.get("offers")) if isinstance(o, dict)), {})
    low = to_decimal(offer.get("lowPrice") if "lowPrice" in offer else offer.get("price"))
    high = to_decimal(offer.get("highPrice")) or low
    sold_out = str(offer.get("availability") or "").endswith("SoldOut")
    performers = [p for p in _list(event.get("performer")) if isinstance(p, dict) and clean_str(p.get("name"))]
    seller = clean_str(offer.get("url"))
    return ScrapedEvent(
        source=AirdropScraper.name,
        source_event_id=urlsplit(url).path.strip("/"),
        url=clean_str(event.get("url")) or url,
        title=title,
        description=html_to_text(str(event.get("description") or "")),
        start_at=start,
        end_at=_local(event.get("endDate"), zone, country),
        venue_name=clean_str(location.get("name")),
        city=clean_str(address.get("addressLocality")),
        country=None if country == "IL" else country,
        source_genres=[g for g in _list(event.get("genre")) if isinstance(g, str)],
        artists=[p["name"].strip() for p in performers],
        artist_profiles=[
            ArtistProfile(name=p["name"].strip(), source=AirdropScraper.name, image_url=clean_str(p.get("image")))
            for p in performers
        ],
        images=[i for i in _list(event.get("image")) if isinstance(i, str)],
        currency=clean_str(offer.get("priceCurrency")) or "ILS",
        min_price=low,
        max_price=high,
        is_sold_out=sold_out,
        tickets=[TicketTier(name="כרטיס", price=low, status=TierStatus.SOLD_OUT if sold_out else TierStatus.ON_SALE)]
        if low is not None
        else [],
        extra={"purchase_url": urlunsplit(urlsplit(seller)._replace(query="", fragment=""))} if seller else {},
    )


def _local(value: Any, zone: str, country: str) -> datetime | None:
    """A start or end; abroad, the site stamps Israel's offset on the venue's wall time, so the
    offset is dropped and the time read in the venue's zone."""
    when = parse_iso(value, zone)
    return when if when is None or country == "IL" else when.replace(tzinfo=None).replace(tzinfo=_zone(zone))


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value] if value else []
