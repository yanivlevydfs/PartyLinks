"""Airdrop (airdrop.co.il) - party guide: Israeli clubs and festivals, and party trips abroad.

robots.txt disallows ``/admin/``, ``/api/``, ``/search.php`` and the account pages, none read.
Listing: ``/events?when=upcoming`` renders every upcoming event as a card (``a.wcard``, 48 a page;
"97 אירועים" over 3 pages on 2026-11-23) linking to its page: ``/<slug>`` for festivals and trips,
``/<club>/<slug>`` for club nights. Every page of it is read, following its ``a.pagination__link``
links (``&page=N``) until no new page turns up. Its genre / country / weekend / month filters
narrow the same list, so the unfiltered pages hold them all.
Details: each page's schema.org ``MusicEvent`` JSON-LD gives name, start/end with offset, place
with a Hebrew city and country, images, genres, performers and one ``Offer`` /
``AggregateOffer`` with the price and the seller's link (Go-Out, Eventer, a travel agency),
kept as ``extra["purchase_url"]`` without Airdrop's ``?ref=`` so dedup ties it to that seller's
own listing. Trips abroad are priced as packages (flight and hotel included).

The listing misses events: a festival's or club's own nights ("/utopia/astrix-dec-22"), the
holiday parties (Purim, New Year) and trips reached only from artist, club and holiday pages. So
every page of ``/sitemap-main.xml`` (~350: events, artists, clubs, cities, holidays) is read
too (``/sitemap-archive.xml``, ~560, holds ended events only, checked 2026-11-23), and each page's
JSON-LD event links (an artist's ``subjectOf``, a club's or holiday's list) are followed until no
new page turns up. A page is an event when its own top-level node is one
(``@context`` or its own URL); past ones are dropped. Artist pages (``MusicArtist``: name,
picture, genre, ``sameAs`` links with Spotify) feed the artist catalog; ``/artists`` (an ``ItemList``)
is read too, so no listed artist depends on the sitemap.

Clubs: ``/clubs`` lists the active clubs (``ItemList``, ~16) and each club page carries a
``NightClub`` (about, street address, coordinates, picture, Instagram / Facebook). They are read
first and kept as venue profiles (``venue_profiles``, the picture as its logo); a club night
(``/<club>/<slug>``, or one held under the club's exact name) gets the club's address and pin,
which its own page lacks, and the profile's key (``VENUE_PROFILE``).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import AsyncIterator
from datetime import datetime
from html import unescape
from typing import Any
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

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
    to_float,
)
from partylinks.parsing import zone as _zone
from partylinks.retention import is_current
from partylinks.scrapers.abroad import country_code, local_zone
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.venue_profiles import VENUE_PROFILE, VenueProfile

_CARD = re.compile(r'<a\s+class="wcard\b[^"]*"\s+href="([^"#]+)"')
_ANCHOR = re.compile(r"<a\b([^>]*)>", re.IGNORECASE)
_CLASS = re.compile(r'\bclass\s*=\s*["\']([^"\']*)["\']', re.IGNORECASE)
_HREF = re.compile(r'\bhref\s*=\s*["\']([^"\'#][^"\']*)["\']', re.IGNORECASE)
_PAGER = re.compile(r'<a\s+class="pagination__link\b[^"]*"\s+href="([^"#]+)"')
_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_EVENT_TYPES = {"event", "musicevent", "festival"}
_CLUB_TYPES = {"nightclub", "musicvenue", "barorpub"}


class AirdropSettings(SourceSettings):
    site_url: str
    listing_path: str
    # Every guide page (events, artists, clubs, holidays): the listing alone misses events.
    sitemap_path: str = "/sitemap-main.xml"
    # The active clubs (read first: their pins go on the club nights) and the artists, as ItemLists.
    clubs_path: str = "/clubs"
    artists_path: str = "/artists"
    # Rounds of following event links found on pages (artist -> event -> club night...); the rounds
    # stop as soon as one finds no new page, so this is only a guard against an endless loop.
    max_link_rounds: int = 10


class AirdropScraper(BaseScraper[AirdropSettings]):
    name = "airdrop"
    settings_model = AirdropSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        urls = await self._listing()
        sitemap = await self.optional("sitemap", self.get_text(s.site_url + s.sitemap_path))
        mapped = sitemap_urls(sitemap or "", s.site_url)
        if not urls:
            # The cards' markup changed (2026-11-17: 0 cards, the whole run failed while the sitemap
            # still listed every event page): the sitemap carries the run, the listing is reported.
            if not mapped:
                raise ParseError("listing has no event cards and the sitemap no pages")
            self.record_failure("listing", ParseError("no event cards (markup changed?): the sitemap's pages only"))
        club_list = await self.optional("clubs", self.get_text(s.site_url + s.clubs_path))
        artist_list = await self.optional("artists", self.get_text(s.site_url + s.artists_path))
        club_urls = item_list_urls(club_list or "", s.site_url)
        self._club_pages: dict[str, str] = {}
        clubs: dict[str, VenueProfile] = {}
        async for club in self.stream_isolated(club_urls, self._fetch_club, label=lambda u: u):
            if club:
                clubs[club.key] = club
        urls = list(dict.fromkeys(urls + mapped + item_list_urls(artist_list or "", s.site_url) + club_urls))
        self.log.debug("listed %d pages, %d clubs", len(urls), len(clubs))
        retention, seen, profiles = RetentionSettings(), set(urls), {}
        for _ in range(s.max_link_rounds + 1):
            found: list[str] = []
            async for page in self.stream_isolated(urls, self._fetch_page, label=lambda u: u):
                event, profile, links = page
                if profile:
                    profiles[profile.profile_url] = profile
                found += [u for u in links if u not in seen and urlsplit(u).hostname == urlsplit(s.site_url).hostname]
                event = at_club(event, clubs) if event else None
                if event and is_current(
                    event.start_at, event.end_at, retention, self.timezone, time_known=event.time_known
                ):
                    yield event
            urls = list(dict.fromkeys(found))
            seen.update(urls)
            if not urls:
                break
        self.artist_profiles += profiles.values()
        self.venue_profiles = await self._with_logos(list(clubs.values()))

    async def _listing(self) -> list[str]:
        """Every listing page's cards: page 1, then each pager link not read yet, until none is left
        (a page past the last repeats the last one, so a page adding no card stops it too)."""
        s = self.settings
        first = s.site_url + s.listing_path
        page = await self.get_text(first)
        urls, read, queue = listing_urls(page, s.site_url), {first}, pager_urls(page, s.site_url)
        while queue:
            url = queue.pop(0)
            if url in read:
                continue
            read.add(url)
            page = await self.optional(f"listing {url}", self.get_text(url)) or ""
            new = [u for u in listing_urls(page, s.site_url) if u not in urls]
            if new:
                urls += new
                queue += [u for u in pager_urls(page, s.site_url) if u not in read]
        self.log.debug("listing: %d cards on %d pages", len(urls), len(read))
        return urls

    async def _fetch_club(self, url: str) -> VenueProfile | None:
        page = await self.get_text(url)
        self._club_pages[url] = page  # read once more for its events and links, without a second request
        return club_profile(page, url)

    async def _fetch_page(self, url: str) -> tuple[ScrapedEvent | None, ArtistProfile | None, list[str]]:
        page = self._club_pages.pop(url, None) or await self.get_text(url)
        return parse_page(page, url, self.timezone)

    async def _with_logos(self, profiles: list[VenueProfile]) -> list[VenueProfile]:
        async def logo(profile: VenueProfile) -> VenueProfile:
            if profile.logo_url:
                response = await self.optional(f"club picture {profile.key}", self.request("GET", profile.logo_url))
                profile.logo = response.content if response is not None else None
            return profile

        return [p async for p in self.stream_isolated(profiles, logo, label=lambda p: p.key)]


def listing_urls(page_html: str, site_url: str) -> list[str]:
    """Event pages the listing's cards link to, in order, once each."""
    hrefs = _CARD.findall(page_html)
    if not hrefs:  # attributes in another order, or a renamed card class (…card…): any <a> with "card" in its class
        for attrs in _ANCHOR.findall(page_html):
            cls, href = _CLASS.search(attrs), _HREF.search(attrs)
            if cls and href and "card" in cls.group(1).lower() and not href.group(1).startswith("#"):
                hrefs.append(href.group(1))
    return list(dict.fromkeys(_canonical(urljoin(site_url + "/", href)) for href in hrefs))


def _canonical(url: str) -> str:
    """One spelling per page, so each page is read and stored once: the path percent-encoded (the
    listing's hrefs and the JSON-LD are, the sitemap's Hebrew ``<loc>``s are not: 4 parties were stored
    under both spellings, 2026-11-23), no trailing slash, no fragment."""
    parts = urlsplit(url)
    path = quote(unquote(parts.path), safe="/:@!$&'()*+,;=-._~").rstrip("/") or "/"
    return urlunsplit(parts._replace(path=path, fragment=""))


def pager_urls(page_html: str, site_url: str) -> list[str]:
    """The listing pages a listing page's pager links to (``&page=N``), once each."""
    return list(dict.fromkeys(urljoin(site_url + "/", unescape(h)) for h in _PAGER.findall(page_html)))


def item_list_urls(page_html: str, site_url: str) -> list[str]:
    """The pages a listing page's ``ItemList`` names (``/clubs``, ``/artists``), in order, once each."""
    home = urlsplit(site_url).hostname
    urls = [
        u
        for n in jsonld_nodes(page_html)
        if "itemlist" in schema_types(n)
        for item in _list(n.get("itemListElement"))
        if isinstance(item, dict) and (u := _absolute(item.get("url"), site_url + "/")) and urlsplit(u).hostname == home
    ]
    return list(dict.fromkeys(urls))


def club_key(url: str) -> str:
    """A club page's profile key, ``airdrop-<slug>``; a Hebrew slug as a short hash (the key is in a URL)."""
    slug = unquote(urlsplit(url).path.strip("/"))
    plain = re.fullmatch(r"[A-Za-z0-9_-]+", slug)
    return f"{AirdropScraper.name}-{slug if plain else hashlib.sha1(slug.encode()).hexdigest()[:12]}"


def club_profile(page_html: str, url: str) -> VenueProfile | None:
    """A club page's ``NightClub``: name, about, street address, pin, picture, links. None on other pages."""
    club = next(
        (n for n in jsonld_nodes(page_html) if schema_types(n) & _CLUB_TYPES and clean_str(n.get("name"))), None
    )
    if club is None:
        return None
    page = _absolute(club.get("url"), url) or url
    address = club.get("address") if isinstance(club.get("address"), dict) else {}
    street, city = clean_str(address.get("streetAddress")), clean_str(address.get("addressLocality"))
    geo = club.get("geo") if isinstance(club.get("geo"), dict) else {}
    lat, lon = to_float(geo.get("latitude")), to_float(geo.get("longitude"))
    pinned = lat is not None and lon is not None
    picture = clean_str(club.get("image")) if isinstance(club.get("image"), str) else None
    name = str(clean_str(club.get("name")))
    links = [clean_str(x) for x in _list(club.get("sameAs")) if isinstance(x, str)]
    return VenueProfile(
        key=club_key(page),
        name=name,
        source=AirdropScraper.name,
        aliases=[name],
        about=html_to_text(str(club.get("description") or "")) or None,
        address=", ".join(p for p in (street, city) if p) or None,
        latitude=lat if pinned else None,
        longitude=lon if pinned else None,
        links=[u for u in links if u and u.startswith("http")],
        profile_url=page,
        logo_url=urljoin(page, picture) if picture else None,
    )


def at_club(event: ScrapedEvent, clubs: dict[str, VenueProfile]) -> ScrapedEvent:
    """A club night with its club's address, pin and profile key (the night's own page names only the
    club and its city). The club is the night's first path part (``/shalvata/<night>``) or its venue name."""
    first, _, rest = event.source_event_id.partition("/")
    club = next((c for c in clubs.values() if rest and urlsplit(c.profile_url or "").path.strip("/") == first), None)
    if club is None and event.venue_name:
        club = next((c for c in clubs.values() if c.name.strip() == event.venue_name.strip()), None)
    if club is None:
        return event
    update: dict[str, Any] = {"extra": {**event.extra, VENUE_PROFILE: club.key}}
    if event.latitude is None and club.latitude is not None:
        update |= {"latitude": club.latitude, "longitude": club.longitude}
    if not event.address and club.address:
        update["address"] = club.address
    return event.model_copy(update=update)


def sitemap_urls(xml: str, site_url: str) -> list[str]:
    """The sitemap's pages on the guide itself, once each (the home page left out)."""
    home = site_url.rstrip("/") + "/"
    return list(dict.fromkeys(_canonical(u) for u in _LOC.findall(xml) if u.startswith(home) and u != home))


def parse_page(page_html: str, url: str, timezone: str) -> tuple[ScrapedEvent | None, ArtistProfile | None, list[str]]:
    """Any guide page: its own event (None when it is no event page or cancelled), the artist it
    is about, and the event pages it links to in its JSON-LD."""
    nodes = jsonld_nodes(page_html)
    links = [u for n in nodes if schema_types(n) & _EVENT_TYPES and (u := _absolute(n.get("url"), url)) and u != url]
    artist = next((n for n in nodes if "musicartist" in schema_types(n) and clean_str(n.get("name"))), None)
    profile = artist_profile(artist, url) if artist else None
    main = _main_event(nodes, url)
    # A destination guide ("/mykonos-parties-festivals") carries an Event with no date: no event page.
    event = parse_event(page_html, url, timezone) if main and main.get("startDate") else None
    return event, profile, list(dict.fromkeys(links))


def artist_profile(artist: dict[str, Any], url: str) -> ArtistProfile:
    """An artist page's ``MusicArtist``: name, picture, first genre, Spotify and the other ``sameAs`` links."""
    same = [
        u
        for u in (clean_str(x) for x in _list(artist.get("sameAs")) if isinstance(x, str))
        if u and u.startswith("http")
    ]
    spotify = next((u for u in same if "spotify.com" in u), None)
    genres = [g for g in (clean_str(x) for x in _list(artist.get("genre")) if isinstance(x, str)) if g]
    return ArtistProfile(
        name=str(clean_str(artist.get("name"))),
        source=AirdropScraper.name,
        genre=genres[0] if genres else None,
        image_url=clean_str(artist.get("image")) if isinstance(artist.get("image"), str) else None,
        spotify_url=spotify,
        profile_url=_absolute(artist.get("url"), url) or url,
        links=[u for u in same if u != spotify],
    )


def _main_event(nodes: list[dict[str, Any]], url: str) -> dict[str, Any] | None:
    """The page's own event node: a top-level one (``@context``) or one naming this page."""
    events = [n for n in nodes if schema_types(n) & _EVENT_TYPES]
    return next((n for n in events if "@context" in n or _absolute(n.get("url"), url) == url), None)


def _absolute(value: Any, base: str) -> str | None:
    href = clean_str(value) if isinstance(value, str) else None
    return _canonical(urljoin(base, href)) if href else None


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
