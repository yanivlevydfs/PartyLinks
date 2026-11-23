from datetime import date, time
from decimal import Decimal
from pathlib import Path

from partylinks.config import get_settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.ybz import (
    YbzSettings,
    hours,
    is_online,
    normalize_town,
    parse_event,
    parse_listing,
    street_part,
)

FIXTURES = Path(__file__).parent / "fixtures" / "ybz"
TZ = "Asia/Jerusalem"


def _settings() -> YbzSettings:
    return YbzSettings(
        cron="43 7,19 * * *",
        venue_name="יד יצחק בן-צבי",
        address="אבן גבירול 14, רחביה",
        city="ירושלים",
        latitude=31.77555,
        longitude=35.2146406,
        labels={"הרצאה": ["הרצאה"], "סיור": ["סיור מודרך"], "יום עיון": ["הרצאה"]},
    )


def _items() -> dict[str, object]:
    html = (FIXTURES / "calendar.html").read_text(encoding="utf-8")
    return {i.url.rstrip("/").rsplit("/", 1)[-1]: i for i in parse_listing(html, "https://ybz.org.il")}


def test_listing_rows() -> None:
    items = _items()
    assert set(items) == {
        "200-days-of-fear",
        "cave-kingdom",
        "bezalel-to-shaarei-hesed",
        "29-november",
        "divided-capital",
    }
    zoom = items["200-days-of-fear"]
    assert zoom.day == date(2026, 11, 24)
    assert zoom.tag == "הרצאה"
    assert (zoom.start, zoom.end) == (time(17, 0), time(18, 0))  # the page writes "18:00 - 17:00"
    assert is_online(zoom, _settings())
    evening = items["29-november"]
    assert evening.place == "יד יצחק בן־צבי"
    assert not is_online(evening, _settings())
    assert items["cave-kingdom"].place is None  # tours have no place line in the calendar


def test_hours_and_town() -> None:
    assert hours("19:00 - 07:00") == (time(7, 0), time(19, 0))
    assert hours("17:30") == (time(17, 30), None)
    assert hours("") == (None, None)
    assert normalize_town("תל־אביב-יפו") == "תל אביב-יפו"
    assert normalize_town("יציאה מירושלים") == "ירושלים"
    assert street_part("מאפיית ברמן – אגריפס 42") == "אגריפס 42"
    assert street_part("ז'ראר בכר – רחוב בצלאל") == "ז'ראר בכר – רחוב בצלאל"  # no house number: the whole line


def test_event_at_the_institute() -> None:
    s = _settings()
    item = _items()["29-november"]
    event = parse_event(item, (FIXTURES / "event.html").read_text(encoding="utf-8"), s, TZ)
    assert event is not None
    assert event.title.startswith("הורה מדינה")
    assert event.start_at.isoformat() == "2026-11-29T19:00:00+02:00"
    assert event.end_at.isoformat() == "2026-11-29T22:00:00+02:00"
    assert (event.venue_name, event.city, event.latitude) == ("יד יצחק בן-צבי", "ירושלים", 31.77555)
    assert event.images and event.images[0].startswith("https://ybz.org.il/wp-content/uploads/")
    assert event.extra["purchase_url"] == item.url
    assert "הרקדה" in (event.description or "")
    enriched = Enricher(get_settings().enrichment, TZ).enrich(event)
    assert enriched is not None and enriched.genres


def test_walking_tour_meets_in_town() -> None:
    s = _settings()
    item = _items()["bezalel-to-shaarei-hesed"]
    event = parse_event(item, (FIXTURES / "tour.html").read_text(encoding="utf-8"), s, TZ)
    assert event is not None
    assert event.venue_name == "ז'ראר בכר – רחוב בצלאל"
    assert event.address == event.venue_name
    assert event.city == "ירושלים"
    assert event.latitude is None  # geocoded from the meeting point
    assert event.min_price == Decimal("120.00")
    assert event.is_sold_out  # "אזל המלאי"
    assert "סיור מודרך" in event.source_genres
    enriched = Enricher(get_settings().enrichment, TZ).enrich(event)
    assert enriched is not None and "tours" in enriched.genres


def test_coach_tour_leaves_from_town() -> None:
    s = _settings()
    item = _items()["cave-kingdom"]
    event = parse_event(item, (FIXTURES / "bus.html").read_text(encoding="utf-8"), s, TZ)
    assert event is not None
    assert event.venue_name == "יציאה מירושלים"
    assert event.city == "ירושלים"
    assert event.address is None and event.latitude is None
    assert event.start_at.isoformat() == "2026-11-24T07:00:00+02:00"
    assert not event.is_sold_out  # "נותרו במלאי 5 כרטיסים"
    assert event.min_price == Decimal("250.00")
