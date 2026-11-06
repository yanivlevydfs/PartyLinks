"""80sMusic: the parties page's JSON-LD MusicEvents."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.parsing import ParseError
from partylinks.scrapers.music80s import parse_event, parse_listing

PAGE_URL = "https://80smusic.co.il/pages/parties"
ITEM = {
    "@context": "https://schema.org",
    "@type": "MusicEvent",
    "name": "ALL NIGHT LONG · חוזרים לפינגווין",
    "startDate": "2026-11-27T22:00:00+03:00",
    "eventStatus": "https://schema.org/EventScheduled",
    "location": {
        "@type": "MusicVenue",
        "name": "OZEN",
        "address": {"@type": "PostalAddress", "addressLocality": "תל אביב", "streetAddress": "קינג ג'ורג' 48, תל אביב"},
    },
    "genre": ["80s", "New Wave", "Dark 80s", "EBM"],
    "performer": [{"@type": "Person", "name": "מיקי חיים"}, {"@type": "Person", "name": "ד״ר שחר שפירו"}],
    "image": "https://80smusic.co.il/images/event-all-night-long.png",
    "offers": {"@type": "Offer", "url": "https://iwant.co.il/allnightlong", "price": 50, "priceCurrency": "ILS"},
}
PAGE = f'<script type="application/ld+json">[{json.dumps(ITEM, ensure_ascii=False)}]</script>'


def test_listing_and_event() -> None:
    [item] = parse_listing(PAGE)
    event = parse_event(item, PAGE_URL, "Asia/Jerusalem")
    assert event is not None
    assert event.title == "ALL NIGHT LONG · חוזרים לפינגווין"
    # The site's "+03:00" in winter is dropped: 22:00 Israel time.
    assert event.start_at.isoformat() == "2026-11-27T22:00:00+02:00"
    assert (event.venue_name, event.address, event.city) == ("OZEN", "קינג ג'ורג' 48, תל אביב", "תל אביב")
    assert event.source_genres == ["80s", "new wave", "dark 80s", "ebm"]
    assert event.artists == ["מיקי חיים", "ד״ר שחר שפירו"]
    assert event.source_event_id == "allnightlong"
    assert event.extra["purchase_url"] == "https://iwant.co.il/allnightlong"
    assert event.min_price == Decimal(50)
    settings = Settings()
    genres = set(Enricher(settings.enrichment, settings.timezone)._detect_genres(event))
    assert {"throwback", "dark_wave"} <= genres


def test_event_id_link_cancelled_and_empty_page() -> None:
    item = ITEM | {"offers": {"url": "https://iwant.co.il/event.php?id=43"}}
    event = parse_event(item, PAGE_URL, "Asia/Jerusalem")
    assert event is not None
    assert event.source_event_id == "event.php?id=43"
    assert event.min_price is None
    assert parse_event(ITEM | {"eventStatus": "https://schema.org/EventCancelled"}, PAGE_URL, "Asia/Jerusalem") is None
    with pytest.raises(ParseError):
        parse_listing("<html></html>")
