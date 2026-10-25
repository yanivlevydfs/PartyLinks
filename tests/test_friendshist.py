"""FRIENDS: Smarticket /api/shows performances with city / address from the WP listing rows
(one show of the real "all shows" page and its API entry, fetched October 2026)."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from partylinks.models import TierStatus
from partylinks.scrapers.friendshist import FriendsHistSettings, build_events, max_page, parse_rows

TZ = "Asia/Jerusalem"
FIXTURES = Path(__file__).parent / "fixtures/friendshist"
LISTING = (FIXTURES / "listing.html").read_text("utf-8")
SHOWS = json.loads((FIXTURES / "shows.json").read_text("utf-8"))
SETTINGS = FriendsHistSettings(
    cron="0 0 * * *",
    site_url="https://friends-hist.co.il",
    listing_path="/all/",
    ajax_path="/wp-admin/admin-ajax.php",
    ajax_action="load_more_shows_all",
    tickets_host="tickets.friends-hist.co.il",
    shows_path="/api/shows",
    event_page_path="/event/{id}",
    uploads_path="/uploads/",
    venue_name="FRIENDS מועדוני תרבות",
)
TITLE = "אףמילהלאמאביתליסין"


def test_listing_rows() -> None:
    assert max_page(LISTING) == 45
    assert parse_rows(LISTING) == {
        # Sold out: no booking popup, so no address.
        ("2026-10-25", "20:30", TITLE): ("תל אביב", None),
        ("2026-10-26", "20:30", TITLE): ("תל אביב", None),
        ("2026-10-31", "20:30", TITLE): ("ראשון לציון", "ז'בוטינסקי 16, ראשון לציון"),
        ("2026-12-17", "20:30", TITLE): ("יקנעם", "האלונים 11 יקנעם, יקנעם"),
    }


def test_events_from_api_and_listing() -> None:
    events = {e.source_event_id: e for e in build_events(SHOWS, parse_rows(LISTING), SETTINGS, TZ)}
    assert list(events) == ["19003", "19004", "19413", "20043", "20045"]

    e = events["19413"]
    assert (e.source, e.title, e.organizer) == ("friendshist", "אף מילה לאמא - בית ליסין", "FRIENDS מועדוני תרבות")
    assert str(e.url) == "https://tickets.friends-hist.co.il/event/19413"
    assert e.start_at is not None and e.start_at.isoformat() == "2026-10-31T20:30:00+02:00"
    assert (e.venue_name, e.city, e.address) == (
        "היכל התרבות מאיר ניצן ראשון לציון",
        "ראשון לציון",
        "ז'בוטינסקי 16, ראשון לציון",
    )
    assert (e.min_price, e.max_price, e.is_sold_out) == (Decimal(59), Decimal(160), False)
    assert e.image_url == "https://tickets.friends-hist.co.il/uploads/upld692fe510a4c27198843684.JPG"
    assert e.source_genres == ["תיאטרון FRIENDS"] and "tenant" not in e.extra

    sold_out = events["19003"]
    assert (sold_out.city, sold_out.address, sold_out.is_sold_out) == ("תל אביב", None, True)
    assert {t.status for t in sold_out.tickets} == {TierStatus.SOLD_OUT}

    # Not on the listing page: city and address come from the same hall's listed performance.
    assert (events["20045"].city, events["20045"].address) == ("יקנעם", "האלונים 11 יקנעם, יקנעם")


def test_without_listing_events_still_come_from_the_api() -> None:
    events = build_events(SHOWS, {}, SETTINGS, TZ)
    assert len(events) == 5 and all(e.city is None and e.venue_name for e in events)
