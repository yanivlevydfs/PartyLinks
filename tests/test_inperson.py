import json
from datetime import UTC, datetime

from partylinks.scrapers.inperson import InpersonSettings, parse_event, parse_listing


def _page(node: dict) -> str:
    return f'<html><script type="application/ld+json">{json.dumps(node, ensure_ascii=False)}</script></html>'


LISTING = _page(
    {
        "@type": "ItemList",
        "itemListElement": [
            {"@type": "ListItem", "url": "https://inperson.social/events/six-16-11", "name": "SIX"},
            {"@type": "ListItem", "url": "https://inperson.social/events/six-16-11", "name": "SIX"},
            {"@type": "ListItem", "url": "https://inperson.social/events/club-18-11", "name": "CLUB"},
        ],
    }
)

SIX = _page(
    {
        "@type": "SocialEvent",
        "name": "SIX",
        "description": "A table for six. Chosen for you.",
        "startDate": "2026-11-16T18:30:00.000Z",
        "endDate": "2026-11-17T20:00:00.000Z",  # a day later: a placeholder
        "image": ["https://firebasestorage.googleapis.com/v0/b/x/o/event-images%2Fmain.png?alt=media"],
        "location": {
            "@type": "Place",
            "name": "Central Tel Aviv · revealed later",
            "address": {"@type": "PostalAddress", "addressLocality": "Tel Aviv", "addressCountry": "IL"},
        },
        "organizer": {"@type": "Organization", "name": "SIX", "url": "https://inperson.social/com/six"},
        "offers": [
            {
                "@type": "Offer",
                "name": "Launch price",
                "price": 49,
                "priceCurrency": "ILS",
                "url": "https://inperson.social/events/six-16-11",
                "availability": "https://schema.org/InStock",
            },
            {
                "@type": "Offer",
                "name": "Regular",
                "price": 89,
                "priceCurrency": "ILS",
                "availability": "https://schema.org/InStock",
            },
        ],
    }
)


def test_listing_urls_once_in_order() -> None:
    assert parse_listing(LISTING) == [
        "https://inperson.social/events/six-16-11",
        "https://inperson.social/events/club-18-11",
    ]


def test_event_hidden_venue_and_placeholder_end_dropped() -> None:
    url = "https://inperson.social/events/six-16-11"
    event = parse_event(SIX, url, InpersonSettings(cron="0 0 * * *"), "Asia/Jerusalem")
    assert event.source == "inperson"
    assert event.source_event_id == "six-16-11"
    assert event.title == "SIX"
    assert event.start_at == datetime(2026, 11, 16, 18, 30, tzinfo=UTC)
    assert event.end_at is None
    assert event.venue_name is None
    assert event.city == "Tel Aviv"
    assert (event.min_price, event.max_price) == (49, 89)
    assert event.images and "firebasestorage" in event.images[0]
    assert event.source_genres == ["פעילות חברתית"]
    assert event.extra["invite_only"] is True


def test_community_names_the_category() -> None:
    settings = InpersonSettings(cron="0 0 * * *", community_genres={"six": ["דייטינג"]})
    event = parse_event(SIX, "https://inperson.social/events/six-16-11", settings, "Asia/Jerusalem")
    assert event.source_genres == ["פעילות חברתית", "דייטינג"]
    assert event.extra["community"] == "six"
