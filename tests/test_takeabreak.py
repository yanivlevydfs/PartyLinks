"""Take A Break: the events page and event pages (Event and TouristTrip JSON-LD) to ScrapedEvent."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from partylinks.config import get_settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.takeabreak import package_prices, parse_event, parse_listing, trip_dates

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
