"""Tel Aviv Museum of Art: the events API's items and an event page, on trimmed real answers."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from partylinks.scrapers.tamuseum import TaMuseumSettings, listing_event, page_details

FIXTURES = Path(__file__).parent / "fixtures" / "tamuseum"
TZ = "Asia/Jerusalem"
SETTINGS = TaMuseumSettings(
    cron="53 7,19 * * *", venue_name="מוזיאון תל אביב לאמנות", address="שאול המלך 27", city="תל אביב-יפו"
)


def test_listing_items_become_events_with_category_and_series() -> None:
    days = json.loads((FIXTURES / "listing.json").read_text(encoding="utf-8"))["items"]
    items = [i for day in days.values() for i in day]
    events = [e for i in items if (e := listing_event(i, SETTINGS, TZ))]
    assert len(events) == len(items)
    concert = events[0]
    assert concert.title == "בדיאלוג: צליל וצבע / על הפיגורטיבי במוזיקה ואמנות"
    assert concert.start_at.isoformat() == "2026-11-03T17:00:00+02:00"
    assert concert.source_genres == ["קונצרט", "בדיאלוג: צליל וצבע / 2027-2026"]
    assert str(concert.url).startswith("https://www.tamuseum.org.il/he/series/dialogue-sound-and-color-2026-2027/")
    # "<i>" tags in titles are dropped; one id on two dates is two events.
    assert all("<i>" not in e.title for e in events)
    # The API repeats an item (same id and start): the scraper keeps one per source_event_id.
    assert len({e.source_event_id for e in events}) == 7 < len(events)


def test_page_gives_artwork_and_the_event_ticket_not_admission() -> None:
    details = page_details((FIXTURES / "event.html").read_text(encoding="utf-8"), ["1366"])
    assert details["images"] and details["images"][0].startswith("https://storage.googleapis.com/tama-static/")
    assert details["tickets"] == "https://tickets.tamuseum.org.il/show/9197"


def _prices(name: str, fragment: str | None = None) -> list[tuple[str, Decimal]]:
    details = page_details((FIXTURES / name).read_text(encoding="utf-8"), ["1366"], fragment)
    return [(t.name, t.price) for t in details["prices"]]


def test_price_row_of_the_details_table() -> None:
    assert _prices("event.html") == [("כרטיס", Decimal(55))]


def test_one_tier_per_price_line_free_entry_included() -> None:
    assert _prices("event_several_prices.html") == [
        ("12/11 - כניסה למוזיאון", Decimal(0)),
        ('אירוע לקראת נעילת התערוכה "עולמות זעירים: מיניאטורות באמנות עכשווית"', Decimal(70)),
        ('סיור מודרך בתערוכה "אלכס ליבק: קופסא שחורה"', Decimal(55)),
    ]


def test_series_page_price_of_the_linked_event_only() -> None:
    assert _prices("series.html", "talk-art-music-avi-nesher-tom-oren") == [("כרטיס", Decimal(140))]
    assert _prices("series.html", "nope") == []
