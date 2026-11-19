"""Take A Break: the events page, event pages (Event / TouristTrip JSON-LD), artist pages and the
destinations' lineup pages to events, artist profiles and venue profiles."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from partylinks.artists import ArtistProfile
from partylinks.config import get_settings
from partylinks.enrichment import Enricher
from partylinks.models import ScrapedEvent
from partylinks.scrapers.takeabreak import (
    Night,
    TakeABreakScraper,
    TakeABreakSettings,
    classify,
    named_artists,
    night_event,
    package_prices,
    parse_artist,
    parse_artist_index,
    parse_event,
    parse_lineup,
    parse_listing,
    parse_venues,
    same_night,
    split_sessions,
    tie_venues,
    trip_dates,
    venue_key,
)
from partylinks.venue_profiles import VENUE_PROFILE

TZ = "Asia/Jerusalem"
SITE = "https://takebreaktravel.com"


def _ld(node: dict) -> str:
    return f'<script type="application/ld+json">{json.dumps(node, ensure_ascii=False)}</script>'


LISTING = (
    _ld(
        {
            "@context": "https://schema.org",
            "@type": "ItemList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "url": f"{SITE}/events/artbat-dubai-28-11"},
                {"@type": "ListItem", "position": 2, "url": f"{SITE}/events/zamna-limassol-2027"},
            ],
        }
    )
    + '<div class="group relative"><div class="relative"><img src="/event-images/a.webp"/>'
    '<div class="absolute right-3 top-3 rounded-full backdrop-blur">הופעה</div></div>'
    '<a aria-label="ARTBAT" class="absolute inset-0" href="/events/artbat-dubai-28-11"></a></div>'
    '<div class="group relative"><div class="relative"><div class="rounded-full backdrop-blur">פסטיבל</div></div>'
    '<a class="absolute inset-0" href="/events/zamna-limassol-2027"></a></div>'
)

# The hotels of the package in the page's React payload, escaped inside a JS string.
HOTELS = (
    '<script>self.__next_f.push([1,"{\\"name\\":\\"PARAMOUNT DUBAI\\",\\"priceLabel\\":\\"\u200f3,260 \u200f₪\\",'
    '\\"deltaLabel\\":null,\\"soldOut\\":false},{\\"name\\":\\"FIVE LUXE\\",\\"priceLabel\\":\\"\u200f5,022 \u200f₪\\",'
    '\\"deltaLabel\\":null,\\"soldOut\\":false},{\\"name\\":\\"ATLANTIS\\",\\"priceLabel\\":\\"\u200f9,999 \u200f₪\\",'
    '\\"deltaLabel\\":null,\\"soldOut\\":true}"])</script>'
)
ARTBAT = (
    _ld(
        {
            "@context": "https://schema.org",
            "@graph": [
                {
                    "@type": "Event",
                    "name": "ARTBAT DUBAI 28.11",
                    "description": "ARTBAT מביאים את UPPERGROUND לדובאי",
                    "startDate": "2026-11-28T20:00:00+04:00",
                    "eventStatus": "https://schema.org/EventScheduled",
                    "image": [f"{SITE}/event-images/artbat-dubai-28-11.webp"],
                    "url": f"{SITE}/events/artbat-dubai-28-11",
                    "performer": [
                        {
                            "@type": "MusicGroup",
                            "name": "ARTBAT",
                            "url": f"{SITE}/artists/artbat",
                            "image": f"{SITE}/artist-images/artbat.webp",
                        }
                    ],
                    "location": {
                        "@type": "Place",
                        "name": "Ushuaïa Dubai Harbour Experience",
                        "address": {
                            "@type": "PostalAddress",
                            "streetAddress": "Dubai Harbour, Dubai",
                            "addressLocality": "דובאי",
                            "addressCountry": "AE",
                        },
                    },
                }
            ],
        }
    )
    + HOTELS
)
TRIP_URL = f"{SITE}/events/zamna-limassol-2027"
TRIP = (
    _ld(
        {
            "@context": "https://schema.org",
            "@graph": [
                {
                    "@type": "BreadcrumbList",
                    "itemListElement": [
                        {"@type": "ListItem", "position": 2, "name": "אירועים", "item": f"{SITE}/events"},
                        {"@type": "ListItem", "position": 3, "name": "זמנה לימסול 2027", "item": TRIP_URL},
                    ],
                },
                {
                    "@type": "TouristTrip",
                    "name": "חבילת טיסה + מלון לזמנה לימסול 2027 (27-31.5.2027)",
                    "description": "טיסה ישירה הלוך-חזור + 4 לילות בלימסול. הכרטיס לפסטיבל נרכש בנפרד.",
                    "itinerary": {"@type": "City", "name": "Limassol", "address": {"addressCountry": "CY"}},
                    "offers": {"@type": "AggregateOffer", "priceCurrency": "USD", "lowPrice": 935, "highPrice": 1999},
                },
            ],
        }
    )
    + '<meta property="og:image" content="https://takebreaktravel.com/cyprus-images/hero.webp"/>'
)


def test_listing_keeps_the_upcoming_events_with_their_kind_badge() -> None:
    assert parse_listing(LISTING, SITE) == {
        f"{SITE}/events/artbat-dubai-28-11": ["הופעה"],
        TRIP_URL: ["פסטיבל"],
    }


def test_event_abroad_in_the_venue_zone_priced_from_the_open_hotels() -> None:
    event = parse_event(ARTBAT, f"{SITE}/events/artbat-dubai-28-11", ["הופעה"], TZ)
    assert event is not None
    assert event.title == "ARTBAT DUBAI 28.11"
    assert event.start_at == datetime(2026, 11, 28, 20, tzinfo=ZoneInfo("Asia/Dubai"))
    assert event.time_known
    assert (event.country, event.time_zone, event.city) == ("AE", "Asia/Dubai", "דובאי")
    assert event.venue_name == "Ushuaïa Dubai Harbour Experience"
    assert (event.min_price, event.max_price, event.currency) == (Decimal(3260), Decimal(5022), "ILS")
    assert event.artists == ["ARTBAT"]
    assert event.artist_profiles[0].profile_url == f"{SITE}/artists/artbat"
    assert event.images == [f"{SITE}/event-images/artbat-dubai-28-11.webp"]
    settings = get_settings()
    enriched = Enricher(settings.enrichment, settings.timezone).enrich(event)
    assert enriched.genres


def test_festival_trip_spans_its_dates_in_the_offer_currency() -> None:
    event = parse_event(TRIP, TRIP_URL, ["פסטיבל"], TZ)
    assert event is not None
    assert event.title == "זמנה לימסול 2027"
    assert event.start_at == datetime(2027, 5, 27, tzinfo=ZoneInfo("Asia/Nicosia"))
    assert event.end_at == datetime(2027, 5, 31, tzinfo=ZoneInfo("Asia/Nicosia"))
    assert not event.time_known
    assert (event.country, event.city, event.venue_name) == ("CY", "Limassol", None)
    assert (event.min_price, event.max_price, event.currency) == (Decimal(935), Decimal(1999), "USD")
    assert event.images == ["https://takebreaktravel.com/cyprus-images/hero.webp"]
    assert event.source_genres == ["פסטיבל"]


def test_cancelled_page_without_event_is_skipped_and_dates_read_from_names() -> None:
    page = "<html><head><title>קיין מיוזיק דובאי 21.11 — בוטל · ADAM PORT במקום | Take A Break</title></head></html>"
    assert parse_event(page, f"{SITE}/events/keinemusik-dubai2026", [], TZ) is None
    z = ZoneInfo("Europe/Paris")
    assert trip_dates("(30.5-2.6.2027)", "Europe/Paris") == (
        datetime(2027, 5, 30, tzinfo=z),
        datetime(2027, 6, 2, tzinfo=z),
    )
    assert trip_dates("סלין דיון 28.5.2027", "Europe/Paris") == (datetime(2027, 5, 28, tzinfo=z), None)
    assert package_prices("<p>no hotels</p>") == (None, None)


# A lineup page as the site builds it (athens-lineup, Nov 2026): a MusicEvent / Event per night in an
# ItemList, a card per night (<li id="date">) with the hour, sold-out note and link, and venue cards.
def _night(name: str, day: str, venue: str, music: bool = True, **more: object) -> dict:
    node = {
        "@type": "MusicEvent" if music else "Event",
        "name": name,
        "startDate": day,
        "url": f"{SITE}/athens-lineup#{day}",
        "image": f"{SITE}/athens-images/hero.webp",
        "location": {"@type": "Place", "name": venue, "address": {"addressLocality": "Athens", "addressCountry": "GR"}},
    }
    return node | more


def _card(day: str, title: str, line: str, href: str = "https://wa.me/972500000000?text=x") -> str:
    return f'<li id="{day}"><a href="{href}"><span>יום</span><h4>{title}</h4><p>{line}</p></a></li>'


LINEUP_URL = f"{SITE}/athens-lineup"
NIKOS_URL = f"{SITE}/events/nikos-vertis-athens"
NIGHTS = [
    _night(
        "Acid Arab — Universe Multivenue",
        "2027-11-27",
        "Universe Multivenue",
        performer=[{"@type": "PerformingGroup", "name": "Acid Arab"}],
    ),
    _night("Nikos Vertis · ניקוס ורטיס — ÉLAN Athens", "2027-12-12", "ÉLAN Athens"),
    _night("Nikos Vertis · ניקוס ורטיס — ÉLAN Athens", "2027-12-19", "ÉLAN Athens"),
    _night(
        "Bryan Adams — Telekom Center Athens",
        "2027-12-19",
        "Telekom Center Athens",
        performer=[{"@type": "PerformingGroup", "name": "Bryan Adams"}],
    ),
    _night("ARTBAT — Gazarte", "2027-12-20", "Gazarte"),
    _night("Regatta of Champions — ILCA 4", "2027-11-22", "Famagusta Nautical Club", music=False),
    _night("קרנבל לימסול — המצעד הגדול — מרכז העיר לימסול", "2028-03-14", "מרכז העיר לימסול", music=False),
    _night("Piaf — The Show — Pattihio Theatre", "2028-01-31", "Pattihio Theatre", music=False),
    _night("Gala Dinner — Hotel X", "2028-02-01", "Hotel X", music=False),
]
LINEUP = (
    _ld(
        {
            "@context": "https://schema.org",
            "@type": "ItemList",
            "itemListElement": [{"@type": "ListItem", "position": i, "item": n} for i, n in enumerate(NIGHTS, start=1)],
        }
    )
    + "<ul>"
    + _card("2027-11-27", "Acid Arab", "Universe Multivenue · 22:00")
    + _card("2027-12-12", "Nikos Vertis · ניקוס ורטיס", "ÉLAN Athens · 23:00", "/events/nikos-vertis-athens")
    + _card("2027-12-19", "Nikos Vertis · ניקוס ורטיס", "ÉLAN Athens · 23:30", "/events/nikos-vertis-athens")
    + _card("2027-12-19", "Bryan Adams", "Telekom Center Athens· 20:00· כרטיסים אזלו")
    + "</ul>"
    + '<div class="rounded-2xl p-6"><div><h3>ÉLAN Athens</h3><p>5<!-- --> אירועים בעונה</p></div>'
    "<p>מועדון הבוזוקי החדש של אתונה.</p></div>"
    '<div class="rounded-2xl p-6"><div><h3>מרכז העיר לימסול</h3><p>2 אירועים בעונה</p></div><p>העיר.</p></div>'
)
NIKOS = _ld(
    {
        "@type": "Event",
        "name": "ניקוס ורטיס באתונה",
        "startDate": "2027-12-12",
        "endDate": "2028-01-24",
        "url": NIKOS_URL,
        "location": {
            "@type": "Place",
            "name": "ÉLAN Athens",
            "address": {"streetAddress": "Neo Faliro, Piraeus", "addressLocality": "אתונה", "addressCountry": "GR"},
        },
        "offers": {"@type": "Offer", "price": 2580, "priceCurrency": "ILS", "url": NIKOS_URL},
    }
)
ARTIST = _ld(
    {
        "@type": "MusicGroup",
        "name": "ARTBAT",
        "genre": "Melodic Techno, Progressive House",
        "image": "/artist-images/artbat.webp",
        "url": f"{SITE}/artists/artbat",
        "sameAs": ["https://soundcloud.com/artbatmusic", "https://www.instagram.com/artbatmusic/"],
    }
)


def _settings() -> TakeABreakSettings:
    return TakeABreakSettings(cron="0 0 * * *")


def _artists() -> dict[str, ArtistProfile]:
    artbat = parse_artist(ARTIST, f"{SITE}/artists/artbat", SITE)
    assert artbat is not None and artbat.profile_url
    return {"artbat": artbat, artbat.profile_url: artbat}


def test_artist_page_gives_picture_genre_and_links_not_the_bio() -> None:
    artbat = _artists()["artbat"]
    assert (artbat.name, artbat.genre, artbat.source) == ("ARTBAT", "Melodic Techno, Progressive House", "takeabreak")
    assert artbat.image_url == f"{SITE}/artist-images/artbat.webp"
    assert artbat.links == ["https://soundcloud.com/artbatmusic", "https://www.instagram.com/artbatmusic/"]
    index = _ld({"@type": "ItemList", "itemListElement": [{"url": f"{SITE}/artists/artbat"}, {"url": f"{SITE}/x"}]})
    assert parse_artist_index(index, SITE) == [f"{SITE}/artists/artbat"]


def test_lineup_nights_take_the_card_hour_in_the_venue_zone_and_the_sold_out_note() -> None:
    s = _settings()
    nights = {(n.day.isoformat(), n.title): n for n in parse_lineup(LINEUP, LINEUP_URL, s)}
    acid = night_event(nights["2027-11-27", "Acid Arab"], TZ, s, {})
    assert acid.start_at == datetime(2027, 11, 27, 22, tzinfo=ZoneInfo("Europe/Athens"))
    assert acid.time_known
    assert (acid.venue_name, acid.city, acid.country, acid.time_zone) == (
        "Universe Multivenue",
        "Athens",
        "GR",
        "Europe/Athens",
    )
    assert acid.url.fragment == "2027-11-27"
    assert acid.artists == ["Acid Arab"]
    assert acid.images == [f"{SITE}/athens-images/hero.webp"]
    bryan = night_event(nights["2027-12-19", "Bryan Adams"], TZ, s, {})
    assert bryan.is_sold_out
    assert bryan.start_at is not None and bryan.start_at.hour == 20
    regatta = night_event(nights["2027-11-22", "Regatta of Champions — ILCA 4"], TZ, s, {})
    assert not regatta.time_known
    assert regatta.start_at == datetime(2027, 11, 22, tzinfo=ZoneInfo("Europe/Athens"))
    carnival = night_event(nights["2028-03-14", "קרנבל לימסול — המצעד הגדול"], TZ, s, {})
    assert carnival.venue_name is None  # a parade through the town centre: no venue pin
    assert nights["2027-12-12", "Nikos Vertis · ניקוס ורטיס"].link == NIKOS_URL


def test_each_kind_is_its_own_category_and_club_nights_are_no_concerts() -> None:
    s = _settings()
    nights = {n.title: n for n in parse_lineup(LINEUP, LINEUP_URL, s)}
    kinds = {t: night_event(n, TZ, s, _artists()).source_genres for t, n in nights.items()}
    assert kinds["Acid Arab"] == ["מסיבה"]  # a club venue
    assert kinds["ARTBAT"] == ["מסיבה", "melodic techno", "progressive house"]  # an electronic artist, named
    assert kinds["Bryan Adams"] == ["הופעה"]
    assert kinds["Regatta of Champions — ILCA 4"] == ["ספורט"]
    assert kinds["קרנבל לימסול — המצעד הגדול"] == ["פסטיבל"]
    assert kinds["Piaf — The Show"] == ["תיאטרון"]
    assert kinds["Gala Dinner"] == ["תרבות"]
    # The site's badge: a match or a festival with DJs stays a match / festival; a DJ "show" is a party.
    assert classify(["ספורט"], "ארסנל נגד דורטמונד", "Emirates Stadium", [], True, s) == ["ספורט"]
    assert classify(["פסטיבל"], "ZAMNA PHUKET", "Yeden Gardens", ["Afro House"], True, s) == ["פסטיבל", "afro house"]
    assert classify(["הופעה"], "MARCO CAROLA DUBAI", "Pacha Dubai", [], True, s) == ["מסיבה"]
    assert classify(["הופעה"], "ELISSA ABU DHABI", "Space42 Arena", [], True, s) == ["הופעה"]
    assert classify(["מסיבה"], "פול מון נובמבר", "Haad Rin Beach", [], False, s) == ["מסיבה"]
    settings = get_settings()
    enricher = Enricher(settings.enrichment, settings.timezone)
    genres = {t: enricher.enrich(night_event(n, TZ, s, _artists())).genres for t, n in nights.items()}
    assert all(genres.values()), genres
    assert "nightclubs" in genres["Acid Arab"]
    assert "live_show" not in genres["Acid Arab"]
    assert "live_show" in genres["Bryan Adams"]
    assert "nightclubs" not in genres["Bryan Adams"]
    assert "sport" in genres["Regatta of Champions — ILCA 4"]
    assert "nightclubs" not in genres["Regatta of Champions — ILCA 4"]
    assert "festival" in genres["קרנבל לימסול — המצעד הגדול"]


def test_a_page_running_on_several_dates_splits_into_its_lineup_nights() -> None:
    s = _settings()
    nights = [n for n in parse_lineup(LINEUP, LINEUP_URL, s) if n.link == NIKOS_URL]
    event = parse_event(NIKOS, NIKOS_URL, ["הופעה"], TZ, s)
    assert event is not None
    assert not event.time_known
    split = split_sessions(event, nights)
    athens = ZoneInfo("Europe/Athens")
    assert [(e.source_event_id, e.start_at, e.time_known, e.end_at) for e in split] == [
        ("nikos-vertis-athens-2027-12-12", datetime(2027, 12, 12, 23, tzinfo=athens), True, None),
        ("nikos-vertis-athens-2027-12-19", datetime(2027, 12, 19, 23, 30, tzinfo=athens), True, None),
    ]
    assert all(e.min_price == Decimal(2580) and e.currency == "ILS" for e in split)
    assert split[0].extra["purchase_url"] == NIKOS_URL
    # One card on the page's own date only lends its hour.
    page = parse_event(NIKOS, NIKOS_URL, ["הופעה"], TZ, s)
    assert page is not None
    one = split_sessions(page, nights[:1])
    assert [(e.source_event_id, e.start_at.hour if e.start_at else None, e.time_known) for e in one] == [
        ("nikos-vertis-athens", 23, True)
    ]


def test_a_lineup_night_that_is_an_events_page_is_not_listed_twice() -> None:
    event = parse_event(ARTBAT, f"{SITE}/events/artbat-dubai-28-11", ["הופעה"], TZ)
    assert event is not None

    def night(day: date, venue: str, performers: list[str]) -> Night:
        return Night(LINEUP_URL, day, "x", True, venue, None, "Dubai", "AE", performers)

    assert same_night(night(date(2026, 11, 28), "Ushuaïa Dubai Harbour Experience · Dubai", []), event)
    assert same_night(night(date(2026, 11, 28), "Elsewhere", ["ARTBAT"]), event)
    assert not same_night(night(date(2026, 11, 29), "Ushuaïa Dubai Harbour Experience", ["ARTBAT"]), event)
    assert not same_night(night(date(2026, 11, 28), "Soho Garden", ["Boris Brejcha"]), event)


def test_lineup_venues_become_venue_profiles_and_their_events_name_them() -> None:
    s = _settings()
    nights = parse_lineup(LINEUP, LINEUP_URL, s)
    profiles = parse_venues(LINEUP, LINEUP_URL, nights, s)
    assert [(p.key, p.name, p.about, p.address, p.profile_url) for p in profiles] == [
        ("takeabreak-elan-athens", "ÉLAN Athens", "מועדון הבוזוקי החדש של אתונה.", "Athens", LINEUP_URL)
    ]  # the town centre is no venue
    events = [night_event(n, TZ, s, {}) for n in nights if n.venue == "ÉLAN Athens"]
    tie_venues(events, profiles)
    assert {e.extra.get(VENUE_PROFILE) for e in events} == {"takeabreak-elan-athens"}
    assert venue_key("מרכז").startswith("takeabreak-")
    assert venue_key("מרכז").isascii()


def test_weekly_residency_is_one_night_a_week_and_a_page_title_names_its_artist() -> None:
    place = {"@type": "Place", "name": "Pacha", "address": {"addressLocality": "Ibiza", "addressCountry": "ES"}}
    weekly = {
        "@type": "Schedule",
        "repeatFrequency": "P1W",
        "byDay": "https://schema.org/Monday",
        "startDate": "2027-05-10",
        "endDate": "2027-05-31",
    }
    residency = _ld(
        {"@type": "MusicEvent", "name": "Sonny Fodera — Pacha", "location": place, "eventSchedule": weekly}
    ) + _ld({"@type": "MusicEvent", "name": "Vintage Culture — Pacha", "location": place})
    nights = parse_lineup(residency, f"{SITE}/ibiza-lineup", _settings(), "מסיבה")
    assert [(n.day, n.title, n.kind) for n in nights] == [
        (date(2027, 5, d), "Sonny Fodera", "מסיבה") for d in (10, 17, 24, 31)
    ]  # an undated residency is skipped
    artists = _artists()
    assert [a.name for a in named_artists("ARTBAT בבוקרשט · 12.12", artists, [])] == ["ARTBAT"]
    assert named_artists("ARTBATMAN", artists, []) == []
    assert named_artists("ARTBAT DUBAI", artists, [{"name": "ARTBAT"}]) == []


def test_run_reads_events_artists_and_lineups_and_lists_each_night_once() -> None:
    import asyncio

    import httpx

    from partylinks.config import Settings
    from partylinks.scrapers import build_scraper

    scraper = build_scraper("takeabreak", Settings())
    assert isinstance(scraper, TakeABreakScraper)
    scraper.http.request_delay_seconds = 0
    scraper.http.backoff_seconds = 0
    scraper.settings.lineup_pages = {"/athens-lineup": None}
    listing = _ld({"@type": "ItemList", "itemListElement": [{"url": NIKOS_URL}]})
    listing += '<div><div class="backdrop-blur">הופעה</div><a href="/events/nikos-vertis-athens"></a></div>'
    pages = {
        "/events": listing,
        "/events/nikos-vertis-athens": NIKOS,
        "/artists": _ld({"@type": "ItemList", "itemListElement": [{"url": f"{SITE}/artists/artbat"}]}),
        "/artists/artbat": ARTIST,
        "/athens-lineup": LINEUP,
    }
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, text=pages[request.url.path])

    async def go() -> list[ScrapedEvent]:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return [e async for e in scraper.scrape()]

    events = asyncio.run(go())
    assert not scraper.failures
    assert sorted(seen) == sorted(pages)  # one request a page, nothing robots.txt disallows
    ids = [e.source_event_id for e in events]
    assert ids[:3] == [
        "athens-lineup-2027-11-22-regatta-of-champions-ilca-4",
        "athens-lineup-2027-11-27-acid-arab",
        "nikos-vertis-athens-2027-12-12",
    ]  # nearest first; the lineup's Nikos nights are the page's, not listed again
    assert ids.count("nikos-vertis-athens-2027-12-19") == 1
    assert not any(i.startswith("athens-lineup") and "nikos" in i for i in ids)
    assert [p.name for p in scraper.artist_profiles] == ["ARTBAT"]
    assert [p.key for p in scraper.venue_profiles] == ["takeabreak-elan-athens"]
    artbat = next(e for e in events if e.title == "ARTBAT")
    assert artbat.artist_profiles[0].genre == "Melodic Techno, Progressive House"
