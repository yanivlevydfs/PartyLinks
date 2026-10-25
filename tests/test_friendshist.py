"""FRIENDS: Smarticket /api/shows performances with city / address from the hall pages'
EventVenue JSON-LD (one show's real API entry, fetched October 2026)."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from partylinks.models import TierStatus
from partylinks.scrapers.friendshist import FriendsHistSettings, build_events, parse_hall

TZ = "Asia/Jerusalem"
FIXTURES = Path(__file__).parent / "fixtures/friendshist"
SHOWS = json.loads((FIXTURES / "shows.json").read_text("utf-8"))
SETTINGS = FriendsHistSettings(
    cron="0 0 * * *",
    site_url="https://friends-hist.co.il",
    hall_sitemap_path="/hall-sitemap.xml",
    tickets_host="tickets.friends-hist.co.il",
    shows_path="/api/shows",
    event_page_path="/event/{id}",
    uploads_path="/uploads/",
    venue_name="FRIENDS מועדוני תרבות",
)


def hall_page(name: str, street: str, city: str) -> str:
    """The Yoast graph of a hall page, as the site renders it (names HTML-escaped)."""
    venue = {
        "@type": "EventVenue",
        "name": name,
        "address": {"@type": "PostalAddress", "streetAddress": street, "addressLocality": city},
    }
    graph = {"@context": "https://schema.org", "@graph": [{"@type": "WebPage", "name": name}, venue]}
    return f'<html><script type="application/ld+json" class="yoast-schema-graph">{json.dumps(graph, ensure_ascii=False)}</script></html>'


HALLS = dict(
    h
    for h in (
        parse_hall(hall_page("היכל התרבות מאיר ניצן ראשון לציון", "ז'בוטינסקי 16", "ראשון לציון")),
        parse_hall(hall_page("היכל תרבות &#8211; תיאטרון יקנעם", "האלונים 11", "יקנעם")),
        parse_hall("<html>no JSON-LD</html>"),
    )
    if h
)


def test_hall_pages() -> None:
    assert len(HALLS) == 2
    # The page's "&#8211;" and the API's "-" normalise to the same key.
    assert HALLS["היכלתרבותתיאטרוןיקנעם"] == ("יקנעם", "האלונים 11")


def test_events_from_api_and_halls() -> None:
    events = {e.source_event_id: e for e in build_events(SHOWS, HALLS, SETTINGS, TZ)}
    assert list(events) == ["19003", "19004", "19413", "20043", "20045"]

    e = events["19413"]
    assert (e.source, e.title, e.organizer) == ("friendshist", "אף מילה לאמא - בית ליסין", "FRIENDS מועדוני תרבות")
    assert str(e.url) == "https://tickets.friends-hist.co.il/event/19413"
    assert e.start_at is not None and e.start_at.isoformat() == "2026-10-31T20:30:00+02:00"
    assert (e.venue_name, e.city, e.address) == ("היכל התרבות מאיר ניצן ראשון לציון", "ראשון לציון", "ז'בוטינסקי 16")
    assert (e.min_price, e.max_price, e.is_sold_out) == (Decimal(59), Decimal(160), False)
    assert e.image_url == "https://tickets.friends-hist.co.il/uploads/upld692fe510a4c27198843684.JPG"
    assert e.source_genres == ["תיאטרון FRIENDS"] and "tenant" not in e.extra

    assert (events["20045"].city, events["20045"].address) == ("יקנעם", "האלונים 11")
    # A hall with no page: no city, the rest of the event stays.
    sold_out = events["19003"]
    assert (sold_out.city, sold_out.address, sold_out.is_sold_out) == (None, None, True)
    assert {t.status for t in sold_out.tickets} == {TierStatus.SOLD_OUT}


def test_without_halls_events_still_come_from_the_api() -> None:
    events = build_events(SHOWS, {}, SETTINGS, TZ)
    assert len(events) == 5 and all(e.city is None and e.venue_name for e in events)
