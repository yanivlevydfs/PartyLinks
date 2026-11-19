"""Take A Break (takebreaktravel.com) - an Israeli agency's packages (flight + hotel, often the ticket) to
club nights, concerts, festivals and matches abroad (Dubai, Athens, Limassol, Mykonos, Ibiza, Phangan...).

robots.txt allows everything but /admin/, /api/, /quote/, /review/ and /seats; nothing here goes there.
A run reads, in this order:

* ``/events`` (one request): an ``ItemList`` JSON-LD of the upcoming event packages only (the sitemap
  keeps past ones too, at "monthly" priority), and each card's kind badge (הופעה, פסטיבל, ספורט).
* ``/artists`` and each artist's page (one request each, 13 in Nov 2026, skipped for an artist already profiled
  with a genre): a ``MusicGroup`` JSON-LD with picture, genre and links (SoundCloud, Instagram). Saved
  as artist profiles; their genre also tells a DJ's club night from a concert. Their bios are not kept.
* The destinations' lineup pages (``lineup_pages``, one request each): a ``MusicEvent`` / ``Event``
  JSON-LD per dated night (venue, city, country, performers) and a card per night with its hour, its
  sold-out note and its link, a WhatsApp quote for the package or the night's own /events page. Ibiza's
  residencies come as weekly ``eventSchedule`` ranges and are expanded to their nights. Each page's
  "where it happens" cards describe its venues: saved as venue profiles. Nights that are an /events
  page's (by link, or by date and venue / performer) are not listed twice; a card linking an /events
  page that runs on several dates (a weekly show) splits that page into one event per date.
* Each /events page (one request each, ~16): a schema.org ``Event`` JSON-LD (name, start with the
  venue's offset, venue with its city and country, artwork, performers with the site's artist page)
  or, for a trip whose festival ticket is sold apart, a ``TouristTrip`` over the trip's dates. Its own
  offer gives the price; otherwise the page's React payload lists the package's hotels, each with a
  per-person price in ILS ("priceLabel") and a sold-out flag: the cheapest and dearest open hotel.

Holiday, ski, honeymoon, group and fan-trip pages carry no dated, bookable event (destinations,
undated packages, a form) and are not read. There are no coordinates: venue and city geocode.
About 24 requests a run once the artists are profiled (37 the first time, Nov 2026).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import urljoin

from pydantic import Field
from selectolax.lexbor import LexborHTMLParser, LexborNode

from partylinks.artists import ArtistProfile
from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import (
    ParseError,
    clean_str,
    has_time,
    html_to_text,
    jsonld_nodes,
    og_image,
    parse_iso,
    schema_types,
    slugify,
    to_decimal,
    zone,
)
from partylinks.retention import is_current
from partylinks.scrapers.abroad import country_code, local_zone
from partylinks.scrapers.base import BaseScraper, SourceSettings
from partylinks.venue_profiles import VENUE_PROFILE, VenueProfile

_EVENT_TYPES = {"event", "musicevent", "festival", "danceevent", "sportsevent"}
# One hotel of the package in the page's React payload (escaped once inside a JS string).
_HOTEL = re.compile(r'priceLabel\\?"\s*:\s*\\?"([^"\\]*)\\?"\s*,[^{}]{0,400}?soldOut\\?"\s*:\s*(true|false)')
_SPAN = re.compile(r"(\d{1,2})(?:\.(\d{1,2}))?\s*[-–]\s*(\d{1,2})\.(\d{1,2})\.(20\d\d)")
_CANCELLED = "בוטל"
_DAY = re.compile(r"(\d{1,2})\.(\d{1,2})\.(20\d\d)")
_MARKS = re.compile("[\u200e\u200f\u202a-\u202e]")
_CLOCK = re.compile(r"(?<![\d:])([01]?\d|2[0-3]):([0-5]\d)(?![\d:])")
_DATE_ID = re.compile(r"^(20\d\d)-(\d\d)-(\d\d)$")
_DASH = " — "
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


class TakeABreakSettings(SourceSettings):
    site_url: str = "https://takebreaktravel.com"
    listing_path: str = "/events"
    artists_path: str = "/artists"
    # Each destination's dated calendar -> the kind of every night on it (None: told night by night).
    lineup_pages: dict[str, str | None] = Field(
        default_factory=lambda: {
            "/dubai-lineup": None,
            "/athens-lineup": None,
            "/limassol-lineup": None,
            "/mykonos-lineup": "מסיבה",  # beach clubs: Scorpios, Alemagou, Santanna, Cavo Paradiso
            "/ibiza-lineup": "מסיבה",  # club residencies: Pacha, Ushuaïa, Hï, [UNVRS], Amnesia
            "/full-moon-2026": "מסיבה",  # Koh Phangan's full / half moon parties
        }
    )
    # Our labels for the kinds (each maps to a site genre through enrichment.genres).
    party_label: str = "מסיבה"
    concert_label: str = "הופעה"
    festival_label: str = "פסטיבל"
    sport_label: str = "ספורט"
    other_label: str = "תרבות"  # a night that is no music event and names no kind
    # Words of a night's title that name its kind.
    kind_words: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "פסטיבל": ["festival", "פסטיבל", "untold", "carnival", "קרנבל"],
            "ספורט": ["marathon", "מרתון", "regatta", "רגטה", "grand prix", "גרנד פרי"],
            "תיאטרון": ["the show", "theatre", "theater", "תיאטרון"],
            "מסיבה": ["party", "מסיבת", "מסיבה", "b2b", "dj set", "full moon", "פול מון", "האלף מון"],
        }
    )
    # Club venues (the site's own lineups): a night there is a club night, not a concert.
    club_venues: list[str] = Field(
        default_factory=lambda: [
            "pacha",
            "ushuaïa",
            "ushuaia",
            "soho garden",
            "16/360",
            "bohemia",
            "universe multivenue",
            "gazarte",
            "burger disco",
            "yas gateway",
            "scorpios",
            "alemagou",
            "santanna",
            "cavoparadiso",
            "cavo paradiso",
            "hï ibiza",
            "unvrs",
            "amnesia",
        ]
    )
    # An artist genre (their /artists page) with one of these is electronic: their night is a club night.
    electronic_words: list[str] = Field(
        default_factory=lambda: ["techno", "house", "electronic", "trance", "afro", "edm", "disco", "dance"]
    )
    sold_out_words: list[str] = Field(default_factory=lambda: ["אזלו", "אזל", "sold out"])
    # A place named so is a whole town, not a venue (a parade, a marathon): no venue, no venue profile.
    not_venues: list[str] = Field(default_factory=lambda: ["מרכז העיר"])
    # The lineup page's venue cards say how many nights each has "this season" ("3 אירועים בעונה").
    venue_card_marker: str = "בעונה"


@dataclass
class Card:
    """A night's card on a lineup page."""

    day: date
    title: str
    clock: time | None = None
    link: str | None = None  # an /events page of the site, absolute
    sold_out: bool = False


@dataclass
class Night:
    """One dated night of a lineup page."""

    page: str  # the lineup page's URL
    day: date
    title: str
    music: bool
    venue: str | None
    street: str | None
    city: str | None
    country: str | None
    performers: list[str] = field(default_factory=list)
    image: str | None = None
    description: str | None = None
    clock: time | None = None
    link: str | None = None
    sold_out: bool = False
    kind: str | None = None  # the lineup page's kind of every night (lineup_pages)


class TakeABreakScraper(BaseScraper[TakeABreakSettings]):
    name = "takeabreak"
    settings_model = TakeABreakSettings
    abroad_only = True

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        listing = parse_listing(await self.get_text(s.site_url + s.listing_path), s.site_url)
        if not listing:
            raise ParseError("events page lists no event")
        self.log.debug("listed %d events", len(listing))
        artists = await self._artists()
        self.artist_profiles += list({id(p): p for p in artists.values()}.values())

        nights: list[Night] = []
        for path, kind in s.lineup_pages.items():
            page = await self.optional(f"lineup {path}", self.get_text(s.site_url + path))
            if page is None:
                continue
            found = parse_lineup(page, s.site_url + path, s, kind)
            nights += found
            self.venue_profiles += parse_venues(page, s.site_url + path, found, s)
        sessions: dict[str, list[Night]] = {}
        for night in nights:
            if night.link in listing:
                sessions.setdefault(night.link, []).append(night)

        async def fetch(item: tuple[str, list[str]]) -> list[ScrapedEvent]:
            url, kinds = item
            event = parse_event(await self.get_text(url), url, kinds, self.timezone, s, artists)
            return split_sessions(event, sessions.get(url, [])) if event else []

        events = [
            e async for found in self.stream_isolated(list(listing.items()), fetch, label=lambda i: i[0]) for e in found
        ]
        for night in nights:
            if night.link in listing or any(same_night(night, e) for e in events):
                continue
            try:
                events.append(night_event(night, self.timezone, s, artists))
            except Exception as exc:  # noqa: BLE001 - one bad night must not cost the others
                self.record_failure(f"night {night.page} {night.day} {night.title}", exc)
        tie_venues(events, self.venue_profiles)
        retention = RetentionSettings()
        for event in sorted(events, key=lambda e: e.start_at or datetime.max.replace(tzinfo=zone("UTC"))):
            if is_current(event.start_at, event.end_at, retention, self.timezone, time_known=event.time_known):
                yield event

    async def _artists(self) -> dict[str, ArtistProfile]:
        """The site's artist profiles by casefolded name and by page URL; a page only for an artist not
        yet profiled with a genre."""
        s = self.settings
        index = await self.optional("artists", self.get_text(s.site_url + s.artists_path))
        known = {p.profile_url: p for p in self.known_artists.values() if p.profile_url and p.genre}
        found: dict[str, ArtistProfile] = {}
        for url in parse_artist_index(index or "", s.site_url):
            profile = known.get(url)
            if profile is None:
                page = await self.optional(f"artist {url}", self.get_text(url))
                profile = parse_artist(page, url, s.site_url) if page else None
            if profile:
                found[profile.name.casefold()] = found[url] = profile
        return found


def parse_listing(page_html: str, site_url: str) -> dict[str, list[str]]:
    """The upcoming events (the ``ItemList``) in order, each with its card's kind badge."""
    base = site_url.rstrip("/")
    urls: list[str] = []
    for node in jsonld_nodes(page_html):
        if "itemlist" in schema_types(node):
            for item in node.get("itemListElement") or []:
                url = clean_str(item.get("url")) if isinstance(item, dict) else None
                if url and url.startswith(base + "/events/"):
                    urls.append(url)
    kinds: dict[str, list[str]] = {}
    for link in LexborHTMLParser(page_html).css('a[href^="/events/"]'):
        card = link.parent
        badge = card.css_first("div.backdrop-blur") if card is not None else None
        text = clean_str(badge.text(strip=True)) if badge is not None else None
        if text:
            kinds.setdefault(base + (link.attributes.get("href") or ""), []).append(text)
    return {u: list(dict.fromkeys(kinds.get(u, []))) for u in dict.fromkeys(urls)}


def parse_artist_index(page_html: str, site_url: str) -> list[str]:
    """The artist pages the /artists page lists, in its order."""
    base = site_url.rstrip("/") + "/artists/"
    urls = [
        url
        for node in jsonld_nodes(page_html)
        if "itemlist" in schema_types(node)
        for item in node.get("itemListElement") or []
        if isinstance(item, dict) and (url := clean_str(item.get("url"))) and url.startswith(base)
    ]
    return list(dict.fromkeys(urls))


def parse_artist(page_html: str, url: str, site_url: str) -> ArtistProfile | None:
    """An artist page's ``MusicGroup`` / ``Person``: name, picture, genre and links (no bio: it is theirs)."""
    for node in jsonld_nodes(page_html):
        if schema_types(node) & {"musicgroup", "person", "performinggroup"} and _same_page(node.get("url"), url):
            name = clean_str(node.get("name"))
            if not name:
                return None
            image = node.get("image") if isinstance(node.get("image"), str) else None
            return ArtistProfile(
                name=name,
                source=TakeABreakScraper.name,
                genre=clean_str(node.get("genre")),
                image_url=urljoin(site_url, image) if image else None,
                profile_url=url,
                links=[u for u in _list(node.get("sameAs")) if isinstance(u, str) and u.startswith("http")],
            )
    return None


def parse_event(
    page_html: str,
    url: str,
    kinds: list[str],
    timezone: str,
    settings: TakeABreakSettings | None = None,
    artists: dict[str, ArtistProfile] | None = None,
) -> ScrapedEvent | None:
    """The page's own ``Event`` (or, for a festival trip whose ticket is sold apart, its ``TouristTrip``
    over the trip's dates) and the package price; None when cancelled."""
    s = settings or TakeABreakSettings(cron="0 0 * * *")
    artists = artists or {}
    nodes = jsonld_nodes(page_html)
    event = next((n for n in nodes if schema_types(n) & _EVENT_TYPES), None)
    trip = None if event else next((n for n in nodes if "touristtrip" in schema_types(n)), None)
    if event is None and trip is None:
        if _CANCELLED in (_title(page_html) or ""):
            return None  # a cancelled event's page keeps only its title ("... — בוטל · ...")
        raise ParseError(f"{url}: no Event or TouristTrip JSON-LD")
    node: dict[str, Any] = event or trip or {}
    if str(node.get("eventStatus") or "").endswith(("EventCancelled", "EventPostponed")):
        return None
    if event:
        title = clean_str(_MARKS.sub("", str(event.get("name") or "")))
        location = event.get("location") if isinstance(event.get("location"), dict) else {}
    else:
        title = _crumb(nodes, url) or clean_str(_MARKS.sub("", str(node.get("name") or "")))
        location = node.get("itinerary") if isinstance(node.get("itinerary"), dict) else {}
    address = location.get("address") if isinstance(location.get("address"), dict) else {}
    street = clean_str(address.get("streetAddress"))
    country = country_code(clean_str(address.get("addressCountry")) or (street or "").rsplit(",", 1)[-1].strip())
    city = clean_str(address.get("addressLocality")) or (None if event else clean_str(location.get("name")))
    tz = local_zone(country, timezone, city) if country != "IL" else timezone
    if event:
        start, end = _local(event.get("startDate"), tz), _local(event.get("endDate"), tz)
        time_known = has_time(event.get("startDate"))
    else:
        start, end = trip_dates(str(node.get("name") or ""), tz)
        time_known = False
    if not title or not start:
        raise ParseError(f"{url}: no name or date")
    performers = [p for p in _list(node.get("performer")) if isinstance(p, dict) and clean_str(p.get("name"))]
    # A page without performers names its artist in the title ("ADAM PORT בבוקרשט"): the artist's page lists it.
    performers += [{"name": a.name, "url": a.profile_url} for a in named_artists(title, artists, performers)]
    names = [p["name"].strip() for p in performers]
    venue = clean_str(location.get("name")) if event else None
    low, high, currency = offer_prices(node.get("offers"))
    if low is None:
        (low, high), currency = package_prices(page_html), "ILS"
    images = [i for i in _list(node.get("image")) if isinstance(i, str)]
    if not images and (og := og_image(page_html)) and "og-default" not in og:
        images = [og]  # a trip page: the destination's picture
    abroad = country != "IL"
    offer = next((o for o in _list(node.get("offers")) if isinstance(o, dict)), {})
    return ScrapedEvent(
        source=TakeABreakScraper.name,
        source_event_id=url.rstrip("/").rsplit("/", 1)[-1],
        url=clean_str(node.get("url")) or url,
        title=title,
        description=html_to_text(str(node.get("description") or "")),
        start_at=start,
        time_known=time_known,
        end_at=end if end and end > start else None,
        venue_name=venue,
        address=street,
        city=city,
        country=country if abroad else None,
        time_zone=tz if abroad else None,
        source_genres=classify(kinds, title, venue, _genres(names, performers, artists), bool(event), s),
        artists=names,
        artist_profiles=[_performer(p, artists, s.site_url) for p in performers],
        images=images,
        currency=currency,
        min_price=low,
        max_price=high,
        extra={"package": True} | ({"purchase_url": u} if (u := clean_str(offer.get("url"))) else {}),
    )


def parse_lineup(page_html: str, page_url: str, settings: TakeABreakSettings, kind: str | None = None) -> list[Night]:
    """A lineup page's dated nights (JSON-LD), each with its card's hour, sold-out note and link; a weekly
    residency (``eventSchedule``) as one night per week of its range."""
    site_url = settings.site_url
    cards: dict[tuple[date, str], Card] = {}
    for card in parse_cards(page_html, site_url, settings.sold_out_words):
        cards.setdefault((card.day, card.title.casefold()), card)
    page_image = og_image(page_html)
    if page_image and "og-default" in page_image:
        page_image = None  # the site's logo card, no picture of the place
    nights: list[Night] = []
    for node in _event_nodes(jsonld_nodes(page_html)):
        location = node.get("location") if isinstance(node.get("location"), dict) else {}
        address = location.get("address") if isinstance(location.get("address"), dict) else {}
        venue = clean_str(location.get("name"))
        name = clean_str(_MARKS.sub("", str(node.get("name") or "")))
        if not name:
            continue
        title = _without_venue(name, venue)
        image = node.get("image") if isinstance(node.get("image"), str) else None
        for day in _days(node):
            card = cards.get((day, title.casefold()))
            nights.append(
                Night(
                    page=page_url,
                    day=day,
                    title=title,
                    music="musicevent" in schema_types(node),
                    venue=venue,
                    street=clean_str(address.get("streetAddress")),
                    city=clean_str(address.get("addressLocality")),
                    country=country_code(clean_str(address.get("addressCountry"))),
                    performers=[
                        n
                        for p in _list(node.get("performer"))
                        if isinstance(p, dict) and (n := clean_str(p.get("name")))
                    ],
                    image=urljoin(site_url, image) if image else page_image,
                    description=clean_str(node.get("description")),
                    clock=card.clock if card else None,
                    link=card.link if card else None,
                    sold_out=card.sold_out if card else False,
                    kind=kind,
                )
            )
    return nights


def parse_cards(page_html: str, site_url: str, sold_out_words: list[str]) -> list[Card]:
    """The lineup's cards: ``<li id="2026-11-27">`` with the day, the title (h4), "venue · area · 22:00"."""
    words = [w.casefold() for w in sold_out_words]
    base = site_url.rstrip("/")
    out = []
    for li in LexborHTMLParser(page_html).css("li[id]"):
        day = _date_id(li.attributes.get("id"))
        heading = li.css_first("h4")
        title = clean_str(heading.text(strip=True)) if heading is not None else None
        if day is None or not title:
            continue
        line = " ".join(p.text(strip=True) for p in li.css("p"))
        clock = _CLOCK.search(line)
        href = _href(li)
        out.append(
            Card(
                day=day,
                title=_MARKS.sub("", title),
                clock=time(int(clock.group(1)), int(clock.group(2))) if clock else None,
                link=base + href if href.startswith("/events/") else None,
                sold_out=any(w in line.casefold() for w in words),
            )
        )
    return out


def parse_venues(
    page_html: str, page_url: str, nights: list[Night], settings: TakeABreakSettings
) -> list[VenueProfile]:
    """The lineup page's "where it happens" cards (name, nights this season, about) as venue profiles,
    with the street and town its nights give."""
    places = {n.venue: n for n in nights if n.venue}
    out = []
    for card in LexborHTMLParser(page_html).css("div.rounded-2xl"):
        heading = card.css_first("h3")
        texts = [p.text(strip=True) for p in card.css("p")]
        if heading is None or len(texts) < 2 or settings.venue_card_marker not in texts[0]:
            continue
        name = clean_str(heading.text(strip=True))
        if not name or any(w in name for w in settings.not_venues):
            continue
        night = places.get(name)
        aliases = list(dict.fromkeys([name, name.split(" · ", 1)[0].strip()]))
        out.append(
            VenueProfile(
                key=venue_key(name),
                name=name,
                source=TakeABreakScraper.name,
                aliases=aliases,
                about=clean_str(texts[-1]),
                address=(", ".join(x for x in (night.street, night.city) if x) or None) if night else None,
                profile_url=page_url,
            )
        )
    return out


def venue_key(name: str) -> str:
    """ASCII key of a venue's profile: "takeabreak-elan-athens"; a hash when the name has no Latin letter."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = slugify(ascii_name)
    return f"{TakeABreakScraper.name}-{slug or hashlib.sha1(name.encode()).hexdigest()[:10]}"


def tie_venues(events: list[ScrapedEvent], profiles: list[VenueProfile]) -> None:
    """Events at a profiled venue name its profile (``extra[VENUE_PROFILE]``)."""
    by_name = {a.casefold(): p.key for p in profiles for a in (p.name, *p.aliases)}
    for event in events:
        if event.venue_name and (key := by_name.get(event.venue_name.casefold())):
            event.extra[VENUE_PROFILE] = key


def night_event(
    night: Night, timezone: str, settings: TakeABreakSettings, artists: dict[str, ArtistProfile]
) -> ScrapedEvent:
    """A lineup night with no page of its own: its card is the booking (a package quote)."""
    tz = local_zone(night.country, timezone, night.city)
    start = datetime.combine(night.day, night.clock or time(0), tzinfo=zone(tz))
    venue = None if night.venue and any(w in night.venue for w in settings.not_venues) else night.venue
    performers = night.performers + [a.name for a in named_artists(night.title, artists, night.performers)]
    genres = _genres(performers, [], artists)
    return ScrapedEvent(
        source=TakeABreakScraper.name,
        source_event_id=f"{night.page.rstrip('/').rsplit('/', 1)[-1]}-{night.day.isoformat()}-{slugify(night.title)[:40]}",
        url=f"{night.page}#{night.day.isoformat()}",
        title=night.title,
        description=night.description,
        start_at=start,
        time_known=night.clock is not None,
        venue_name=venue,
        address=night.street,
        city=night.city,
        country=night.country if night.country != "IL" else None,
        time_zone=tz if night.country != "IL" else None,
        source_genres=classify([night.kind] if night.kind else [], night.title, venue, genres, night.music, settings),
        artists=performers,
        artist_profiles=[artists[n.casefold()] for n in performers if n.casefold() in artists],
        images=[night.image] if night.image else [],
        is_sold_out=night.sold_out,
        extra={"package": True},
    )


def split_sessions(event: ScrapedEvent, nights: list[Night]) -> list[ScrapedEvent]:
    """An /events page and the lineup cards that link it: a page running on several dates (a weekly
    show over six weeks) becomes one event per date, each at its card's hour; one date only takes the
    card's hour when the page has none."""
    first = event.start_at.date() if event.start_at else None
    last = event.end_at.date() if event.end_at else first
    days = sorted({n.day: n for n in nights if first and last and first <= n.day <= last}.items())
    if not days or event.start_at is None:
        return [event]
    tz = event.start_at.tzinfo
    if len(days) == 1:
        day, night = days[0]
        if not event.time_known and night.clock and day == first:
            event.start_at = datetime.combine(day, night.clock, tzinfo=tz)
            event.time_known = True
        return [event]
    out = []
    for day, night in days:
        clock = night.clock if night.clock else None
        out.append(
            event.model_copy(
                deep=True,
                update={
                    "source_event_id": f"{event.source_event_id}-{day.isoformat()}",
                    "start_at": datetime.combine(day, clock or time(0), tzinfo=tz),
                    "time_known": clock is not None,
                    "end_at": None,
                    "is_sold_out": event.is_sold_out or night.sold_out,
                },
            )
        )
    return out


def same_night(night: Night, event: ScrapedEvent) -> bool:
    """A lineup night that is an /events page's: the same local day, at the same venue or with one of
    its performers in the page's title ("Elissa" in "ELISSA ABU DHABI 19.12")."""
    if event.start_at is None or event.start_at.date() != night.day:
        return False
    a, b = _venue_words(night.venue), _venue_words(event.venue_name)
    if a and b and (a in b or b in a):
        return True
    title = event.title.casefold()
    return any(len(p) > 2 and p.casefold() in title for p in night.performers) or any(
        len(p) > 2 and p.casefold() in night.title.casefold() for p in event.artists
    )


def classify(
    badges: list[str],
    title: str,
    venue: str | None,
    artist_genres: list[str],
    music: bool,
    settings: TakeABreakSettings,
) -> list[str]:
    """The kind labels of an event: the site's badge, kind words of its title, then a club night (an
    electronic artist, a club venue or a party word) instead of a concert unless it is a festival or a
    match; the artists' own genres after them."""
    s = settings
    labels = [b for b in badges if b]
    low = title.casefold()
    for label, words in s.kind_words.items():
        if label not in labels and any(w in low for w in words):
            labels.append(label)
    club = bool(venue) and any(v in venue.casefold() for v in s.club_venues)
    electronic = any(w in g.casefold() for g in artist_genres for w in s.electronic_words)
    if (club or electronic or s.party_label in labels) and not {s.festival_label, s.sport_label} & set(labels):
        labels = [x for x in labels if x != s.concert_label]
        if s.party_label not in labels:
            labels.insert(0, s.party_label)
    if not labels:
        labels = [s.concert_label if music else s.other_label]
    genres = [g.strip() for text in artist_genres for g in text.split(",") if g.strip()]
    return list(dict.fromkeys(x.lower() for x in [*labels, *genres]))


def offer_prices(offers: Any) -> tuple[Decimal | None, Decimal | None, str]:
    """The JSON-LD offer's "from" price (or low / high) and its currency."""
    offer = next((o for o in _list(offers) if isinstance(o, dict)), {})
    low = to_decimal(offer.get("lowPrice") if "lowPrice" in offer else offer.get("price"))
    high = to_decimal(offer.get("highPrice")) or low
    return low or None, high or None, clean_str(offer.get("priceCurrency")) or "ILS"


def trip_dates(name: str, tz: str) -> tuple[datetime | None, datetime | None]:
    """The trip's days from its name: "(27-31.5.2027)", "(30.5-2.6.2027)" or "28.5.2027"."""
    when = None
    if m := _SPAN.search(name):
        d1, m1, d2, m2, year = m.groups()
        when = (int(year), int(m1 or m2), int(d1)), (int(year), int(m2), int(d2))
    elif m := _DAY.search(name):
        day, month, year = map(int, m.groups())
        when = (year, month, day), None
    if when is None:
        return None, None
    try:
        start = datetime(*when[0], tzinfo=zone(tz))
        end = datetime(*when[1], tzinfo=zone(tz)) if when[1] else None
    except ValueError:
        return None, None
    return start, end if end and end > start else None


def package_prices(page_html: str) -> tuple[Decimal | None, Decimal | None]:
    """Cheapest and dearest per-person price of the package's open hotels (ILS)."""
    prices = []
    for label, sold_out in _HOTEL.findall(page_html):
        digits = re.sub(r"\D", "", label)
        if digits and sold_out == "false":
            prices.append(Decimal(digits))
    return (min(prices), max(prices)) if prices else (None, None)


def _event_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every event node (``jsonld_nodes`` also yields those inside an ``ItemList``'s items), each once."""
    unique: dict[tuple[str, str, str], dict[str, Any]] = {}
    for node in nodes:
        if schema_types(node) & _EVENT_TYPES:
            place = node.get("location") if isinstance(node.get("location"), dict) else {}
            unique.setdefault((str(node.get("name")), str(node.get("startDate")), str(place.get("name"))), node)
    return list(unique.values())


def _days(node: dict[str, Any]) -> list[date]:
    """A night's date; a weekly residency's every night of its range ("P1W" on its weekday)."""
    first = _date_of(node.get("startDate"))
    schedule = node.get("eventSchedule") if isinstance(node.get("eventSchedule"), dict) else None
    if schedule and str(schedule.get("repeatFrequency") or "").upper() == "P1W":
        start = _date_of(schedule.get("startDate")) or first
        end = _date_of(schedule.get("endDate")) or _date_of(node.get("endDate"))
        weekdays = {
            _WEEKDAYS.index(d) for v in _list(schedule.get("byDay"))
            if isinstance(v, str) and (d := v.rstrip("/").rsplit("/", 1)[-1].casefold()) in _WEEKDAYS
        }  # fmt: skip
        if start and end and weekdays and end >= start:
            return [
                start + timedelta(days=i)
                for i in range((end - start).days + 1)
                if (start + timedelta(days=i)).weekday() in weekdays
            ]
    return [first] if first else []


def _date_of(value: Any) -> date | None:
    text = str(value or "")[:10]
    return _date_id(text)


def _date_id(value: Any) -> date | None:
    m = _DATE_ID.match(str(value or ""))
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _without_venue(name: str, venue: str | None) -> str:
    """The night's name without its venue part: "Acid Arab — Universe Multivenue" / "Alemagou — CamelPhat"."""
    parts = [p.strip() for p in name.split(_DASH)]
    if venue and len(parts) > 1:
        rest = [p for p in parts if p.casefold() != venue.casefold()]
        if rest:
            return _DASH.join(rest)
    return name


def _venue_words(name: str | None) -> str:
    """A venue's name to compare: its first part, letters and digits only ("Space42 Arena · אבו דאבי")."""
    first = (name or "").split("·", 1)[0].split(",", 1)[0]
    return re.sub(r"\W+", "", unicodedata.normalize("NFKD", first).casefold())


def named_artists(title: str, artists: dict[str, ArtistProfile], performers: list[Any]) -> list[ArtistProfile]:
    """The site's artists whose name stands as a word in the title and are not among the performers yet."""
    have = {(p.get("name") if isinstance(p, dict) else p or "").strip().casefold() for p in performers}
    low = title.casefold()
    found = {id(a): a for a in artists.values() if a.name.casefold() not in have}
    return [a for a in found.values() if re.search(rf"(?<!\w){re.escape(a.name.casefold())}(?!\w)", low)]


def _genres(names: list[str], performers: list[dict[str, Any]], artists: dict[str, ArtistProfile]) -> list[str]:
    """The site's genres of these performers (their /artists pages)."""
    found = []
    for i, name in enumerate(names):
        url = clean_str(performers[i].get("url")) if i < len(performers) else None
        profile = (artists.get(url) if url else None) or artists.get(name.casefold())
        if profile and profile.genre:
            found.append(profile.genre)
    return found


def _performer(p: dict[str, Any], artists: dict[str, ArtistProfile], site_url: str) -> ArtistProfile:
    """A page's performer: their /artists profile when read, else the link and picture the event gives."""
    url = clean_str(p.get("url"))
    image = clean_str(p.get("image")) if isinstance(p.get("image"), str) else None
    known = (artists.get(url) if url else None) or artists.get(p["name"].strip().casefold())
    if known:
        return known
    return ArtistProfile(
        name=p["name"].strip(),
        source=TakeABreakScraper.name,
        image_url=urljoin(site_url, image) if image else None,
        profile_url=urljoin(site_url, url) if url else None,
    )


def _href(li: LexborNode) -> str:
    link = li.css_first("a")
    return (link.attributes.get("href") or "") if link is not None else ""


def _same_page(value: Any, url: str) -> bool:
    return isinstance(value, str) and value.rstrip("/").rsplit("/", 1)[-1] == url.rstrip("/").rsplit("/", 1)[-1]


def _title(page_html: str) -> str | None:
    node = LexborHTMLParser(page_html).css_first("title")
    return node.text(strip=True) if node is not None else None


def _crumb(nodes: list[dict[str, Any]], url: str) -> str | None:
    """The breadcrumb's name of this page ("זמנה לימסול 2027")."""
    for node in nodes:
        if "breadcrumblist" in schema_types(node):
            for item in node.get("itemListElement") or []:
                if isinstance(item, dict) and str(item.get("item") or "").rstrip("/") == url.rstrip("/"):
                    return clean_str(_MARKS.sub("", str(item.get("name") or "")))
    return None


def _local(value: Any, tz: str) -> datetime | None:
    """A start or end in the venue's zone."""
    when = parse_iso(value, tz)
    return when.astimezone(zone(tz)) if when else None


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value] if value else []
