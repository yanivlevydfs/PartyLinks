import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from partylinks.config import get_settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.kfarsabamuseum import (
    KfarSabaMuseumSettings,
    Place,
    keep_occurrence,
    page_text,
    parse_occurrence,
    product_day,
    product_tiers,
)

FIXTURES = Path(__file__).parent / "fixtures" / "kfarsabamuseum"
TZ = "Asia/Jerusalem"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _day(value: datetime | None) -> date | None:
    return value.date() if value is not None else None


def _settings() -> KfarSabaMuseumSettings:
    return KfarSabaMuseumSettings(
        cron="9 8,20 * * *",
        city="כפר סבא",
        places={
            "בית הבאר": Place(
                venue_name="בית הבאר - מוזאון כפר סבא", address="ויצמן 135", latitude=32.1746487, longitude=34.9123102
            ),
            "גן הברון מנשה": Place(
                venue_name="גן הברון מנשה", address="מול אנגל 37", latitude=32.1960436, longitude=34.8981185
            ),
        },
        labels={"פסטיבל בתים פתוחים": ["פסטיבל"]},
    )


def _load(name: str) -> list[dict]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _occurrence(event_id: int) -> dict:
    return next(o for o in _load("occurrences.json") if o["eventId"] == event_id)


def _post(event_id: int) -> dict:
    return next(p for p in _load("events.json") if p["id"] == event_id)


def test_keep_only_coming_active_performances() -> None:
    s = _settings()
    assert keep_occurrence(_occurrence(2276), s, TZ)
    assert not keep_occurrence(_occurrence(2053), s, TZ)  # "(הסתיים)": has_passed
    postponed = {**_occurrence(2276), "title": "**נדחה** המסע האלקטרוני של סייד דרוויש"}
    assert not keep_occurrence(postponed, s, TZ)
    assert not keep_occurrence({**_occurrence(2276), "status": "cancelled"}, s, TZ)


def test_concert_at_beit_habeer() -> None:
    s = _settings()
    text = page_text((FIXTURES / "tal_gur_duo.html").read_text(encoding="utf-8"))
    event = parse_occurrence(_occurrence(2276), _post(2276), _load("products.json"), text, s, TZ)
    assert event is not None
    assert event.title == "דואו טל גור - מופע לחודש האזרח הותיק"
    assert _iso(event.start_at) == "2026-12-15T20:30:00+02:00"
    assert event.start_at is not None and event.end_at is not None and event.end_at > event.start_at
    assert (event.venue_name, event.city, event.latitude) == ("בית הבאר - מוזאון כפר סבא", "כפר סבא", 32.1746487)
    assert "אירועים קרובים" not in event.source_genres  # the home shelf, no category
    assert "מוזיקה" in event.source_genres
    assert event.min_price == Decimal(25)
    assert event.images and event.images[0].startswith("https://kfar-saba-museum.org/wp-content/uploads/")
    assert "-300x300" not in event.images[0]  # the full-size artwork, not the calendar's thumbnail
    assert "ג'אז" in (event.description or "")
    assert event.extra["purchase_url"] == "https://kfar-saba-museum.org/events/tal_gur_duo/"
    enriched = Enricher(get_settings().enrichment, TZ).enrich(event)
    assert enriched is not None and "live_show" in enriched.genres


def test_family_day_tiers() -> None:
    s = _settings()
    event = parse_occurrence(_occurrence(2724), _post(2724), _load("products.json"), None, s, TZ)
    assert event is not None
    assert event.venue_name == "גן הברון מנשה"
    assert {t.name: t.price for t in event.tickets} == {"בודד": Decimal(45), "משפחתי (4 משתתפים)": Decimal(145)}
    enriched = Enricher(get_settings().enrichment, TZ).enrich(event)
    assert enriched is not None and "kids" in enriched.genres


def test_unknown_place_is_geocoded_and_product_day() -> None:
    s = _settings()
    row = {**_occurrence(2276), "location": "צריף הסנדלר, הרצל 48 כפר סבא"}
    event = parse_occurrence(row, _post(2276), [], None, s, TZ)
    assert event is not None
    assert event.venue_name == event.address == "צריף הסנדלר, הרצל 48 כפר סבא"
    assert event.latitude is None and event.city == "כפר סבא"
    assert product_day("הענק, הברון וגנו – בודד (יום שני, ה-7 בדצמבר 2026 בשעה 09:30 – גן הברון מנשה)") == date(
        2026, 12, 7
    )
    assert (
        product_tiers(
            [{"name": "כרטיס כניסה לביקור בגן הברון מנשה", "prices": {"price": "2000"}}], "הענק", date(2026, 12, 7)
        )
        == []
    )
