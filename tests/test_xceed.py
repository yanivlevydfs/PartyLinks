"""Xceed: city-page React state and event-page JSON-LD to ScrapedEvent."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from partylinks.config import get_settings
from partylinks.enrichment import Enricher
from partylinks.models import TierStatus
from partylinks.parsing import ParseError
from partylinks.scrapers.xceed import city_events, event_url, parse_event

TZ = "Asia/Jerusalem"
ITEM = {
    "id": "dbe76dc8",
    "legacyId": 242764,
    "name": "PLAY CUBE RECORDS | MAIN ROOM",
    "slug": "play-cube-records-main-room",
    "startingTime": 1794956340,
    "endingTime": 1794974400,
    "coverUrl": "https://images.xceed.me/events/banners/play-cube.png",
    "venue": {"name": "City Hall", "slug": "city-hall", "city": {"name": "Barcelona", "slug": "barcelona", "timezone": "Europe/Madrid"}},
    "musicGenres": [{"name": "Techno", "slug": "techno"}, {"name": "Tech House", "slug": "tech-house"}],
}  # fmt: skip


def _city_page(items: list[dict]) -> str:
    state = {
        "dehydratedAt": 1,
        "state": {"data": {"pages": [{"items": items}]}},
        "queryKey": ["all-events", {"params": {"cities[]": ["barcelona"]}}],
    }
    flight = "1:" + json.dumps({"queries": [state]})
    return f"<script>self.__next_f.push([1,{json.dumps(flight)}])</script>"


def _event_page(offers: list[dict]) -> str:
    node = {
        "@context": "https://schema.org",
        "@type": "Event",
        "name": "PLAY CUBE RECORDS | MAIN ROOM",
        "url": "https://xceed.me/en/barcelona/event/play-cube-records-main-room/242764",
        "startDate": "2026-11-20T23:59:00+01:00",
        "endDate": "2026-11-21T06:00:00+01:00",
        "eventStatus": "https://schema.org/EventScheduled",
        "image": ["https://images.xceed.me/events/banners/play-cube.png?w=1920"],
        "performer": [{"@type": "MusicGroup", "name": "Artdetta", "url": "https://xceed.me/en/artist/artdetta"}],
        "location": {
            "@type": "NightClub",
            "name": "City Hall",
            "address": {
                "streetAddress": "Rambla de Catalunya 2",
                "addressLocality": "Barcelona",
                "addressCountry": "ES",
            },
            "geo": {"latitude": 41.387721, "longitude": 2.168269},
        },
        "offers": offers,
    }
    return f'<script type="application/ld+json">{json.dumps(node)}</script>'


def test_city_page_items_and_event_url() -> None:
    items = city_events(_city_page([ITEM, {"name": "no id"}]))
    assert [i["legacyId"] for i in items] == [242764]
    assert event_url(items[0], "https://xceed.me", "en") == (
        "https://xceed.me/en/barcelona/event/play-cube-records-main-room/242764"
    )
    with pytest.raises(ParseError):
        city_events("<html></html>")


def test_event_in_the_venue_zone_with_coordinates_tiers_and_the_offer_currency() -> None:
    offers = [
        {"name": "Early", "price": 10, "priceCurrency": "EUR", "availability": "https://schema.org/SoldOut"},
        {"name": "General", "price": 15, "priceCurrency": "EUR", "availability": "https://schema.org/InStock"},
        {"name": "VIP", "price": 40, "priceCurrency": "EUR", "availability": "https://schema.org/InStock"},
    ]
    event = parse_event(_event_page(offers), ITEM, "u", "nightlife", TZ)
    assert event is not None
    madrid = ZoneInfo("Europe/Madrid")
    assert event.start_at == datetime(2026, 11, 20, 23, 59, tzinfo=madrid)
    assert event.end_at == datetime(2026, 11, 21, 6, tzinfo=madrid)
    assert (event.country, event.time_zone, event.city) == ("ES", "Europe/Madrid", "Barcelona")
    assert (event.latitude, event.longitude) == (41.387721, 2.168269)
    assert (event.min_price, event.max_price, event.currency) == (Decimal(15), Decimal(40), "EUR")
    assert [t.status for t in event.tickets] == [TierStatus.SOLD_OUT, TierStatus.ON_SALE, TierStatus.ON_SALE]
    assert not event.is_sold_out
    assert event.source_genres == ["techno", "tech house"]
    assert event.artists == ["Artdetta"]
    settings = get_settings()
    assert Enricher(settings.enrichment, settings.timezone).enrich(event).genres


def test_unpriced_night_without_genres_and_cancelled_event() -> None:
    event = parse_event(_event_page([]), ITEM | {"musicGenres": []}, "u", "nightlife", TZ)
    assert event is not None
    assert (event.min_price, event.tickets, event.source_genres) == (None, [], ["nightlife"])
    cancelled = _event_page([]).replace("EventScheduled", "EventCancelled")
    assert parse_event(cancelled, ITEM, "u", "nightlife", TZ) is None
