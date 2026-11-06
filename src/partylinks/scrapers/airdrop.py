"""Airdrop (airdrop.co.il) - party guide: Israeli clubs and festivals, and party trips abroad.

robots.txt allows everything but ``/admin/``. Listing: ``/events?when=upcoming`` renders every
upcoming event as a card (``a.wcard``, ~50) linking to its page: ``/<slug>`` for festivals and
trips, ``/<club>/<slug>`` for club nights. (The sitemap is mostly past events, ~600 pages.)
Details: each page's schema.org ``MusicEvent`` JSON-LD gives name, start/end with offset, place
with a Hebrew city and country, images, genres, performers and one ``Offer`` /
``AggregateOffer`` with the price and the seller's link (Go-Out, Eventer, a travel agency),
kept as ``extra["purchase_url"]`` without Airdrop's ``?ref=`` so dedup ties it to that seller's
own listing. Trips abroad are priced as packages (flight and hotel included).

The listing misses events: a festival's or club's own nights ("/utopia/astrix-dec-22"), the
holiday parties (Purim, New Year) and trips reached only from artist, club and holiday pages. So
every page of ``/sitemap-main.xml`` (~320: events, artists, clubs, cities, holidays) is read
too, and each page's JSON-LD event links (an artist's ``subjectOf``, a club's or holiday's list)
are followed until no new page turns up. A page is an event when its own top-level node is one
(``@context`` or its own URL); past ones are dropped. Artist pages (``MusicArtist``: name,
picture) feed the artist catalog.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from partylinks.artists import ArtistProfile
from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier, TierStatus
from partylinks.parsing import (
    ParseError,
    clean_str,
    has_time,
    html_to_text,
    jsonld_nodes,
    parse_iso,
    schema_types,
    to_decimal,
)
from partylinks.parsing import zone as _zone
from partylinks.retention import is_current
from partylinks.scrapers.abroad import country_code, local_zone
from partylinks.scrapers.base import BaseScraper, SourceSettings

_CARD = re.compile(r'<a\s+class="wcard\b[^"]*"\s+href="([^"#]+)"')
_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_EVENT_TYPES = {"event", "musicevent", "festival"}


class AirdropSettings(SourceSettings):
    site_url: str
    listing_path: str
    # Every guide page (events, artists, clubs, holidays): the listing alone misses events.
    sitemap_path: str = "/sitemap-main.xml"
    # Rounds of following event links found on pages (artist -> event -> club night...).
    max_link_rounds: int = 3


class AirdropScraper(BaseScraper[AirdropSettings]):
    name = "airdrop"
    settings_model = AirdropSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        urls = listing_urls(await self.get_text(s.site_url + s.listing_path), s.site_url)
        if not urls:
            raise ParseError("listing has no event cards")
        sitemap = await self.optional("sitemap", self.get_text(s.site_url + s.sitemap_path))
        urls = list(dict.fromkeys(urls + sitemap_urls(sitemap or "", s.site_url)))
        self.log.debug("listed %d pages", len(urls))
        retention, seen, profiles = RetentionSettings(), set(urls), {}
        for _ in range(s.max_link_rounds + 1):
            found: list[str] = []
            async for page in self.stream_isolated(urls, self._fetch_page, label=lambda u: u):
                event, profile, links = page
                if profile:
                    profiles[profile.profile_url] = profile
                found += [u for u in links if u not in seen and urlsplit(u).hostname == urlsplit(s.site_url).hostname]
                if event and is_current(
                    event.start_at, event.end_at, retention, self.timezone, time_known=event.time_known
                ):
                    yield event
            urls = list(dict.fromkeys(found))
            seen.update(urls)
            if not urls:
                break
        self.artist_profiles += profiles.values()

    async def _fetch_page(self, url: str) -> tuple[ScrapedEvent | None, ArtistProfile | None, list[str]]:
        return parse_page(await self.get_text(url), url, self.timezone)


def listing_urls(page_html: str, site_url: str) -> list[str]:
    """Event pages the listing's cards link to, in order, once each."""
    return list(dict.fromkeys(urljoin(site_url + "/", href) for href in _CARD.findall(page_html)))


def sitemap_urls(xml: str, site_url: str) -> list[str]:
    """The sitemap's pages on the guide itself, once each (the home page left out)."""
    home = site_url.rstrip("/") + "/"
    return list(dict.fromkeys(u for u in _LOC.findall(xml) if u.startswith(home) and u != home))


def parse_page(page_html: str, url: str, timezone: str) -> tuple[ScrapedEvent | None, ArtistProfile | None, list[str]]:
    """Any guide page: its own event (None when it is no event page or cancelled), the artist it
    is about, and the event pages it links to in its JSON-LD."""
    nodes = jsonld_nodes(page_html)
    links = [u for n in nodes if schema_types(n) & _EVENT_TYPES and (u := _absolute(n.get("url"), url)) and u != url]
    artist = next((n for n in nodes if "musicartist" in schema_types(n) and clean_str(n.get("name"))), None)
    profile = (
        ArtistProfile(
            name=str(clean_str(artist.get("name"))),
            source=AirdropScraper.name,
            image_url=clean_str(artist.get("image")) if isinstance(artist.get("image"), str) else None,
            profile_url=_absolute(artist.get("url"), url) or url,
        )
        if artist
        else None
    )
    main = _main_event(nodes, url)
    # A destination guide ("/mykonos-parties-festivals") carries an Event with no date: no event page.
    event = parse_event(page_html, url, timezone) if main and main.get("startDate") else None
    return event, profile, list(dict.fromkeys(links))


def _main_event(nodes: list[dict[str, Any]], url: str) -> dict[str, Any] | None:
    """The page's own event node: a top-level one (``@context``) or one naming this page."""
    events = [n for n in nodes if schema_types(n) & _EVENT_TYPES]
    return next((n for n in events if "@context" in n or _absolute(n.get("url"), url) == url), None)


def _absolute(value: Any, base: str) -> str | None:
    href = clean_str(value) if isinstance(value, str) else None
    return urlunsplit(urlsplit(urljoin(base, href))._replace(fragment="")) if href else None


def parse_event(page_html: str, url: str, timezone: str) -> ScrapedEvent | None:
    """The page's own ``MusicEvent``; None when cancelled."""
    event = _main_event(jsonld_nodes(page_html), url)
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
        time_known=has_time(event.get("startDate")),
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
