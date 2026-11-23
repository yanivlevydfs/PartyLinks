from datetime import date, datetime, time
from pathlib import Path

from partylinks.config import get_settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.mjc import Card, MjcSettings, build_event, parse_listing, post_text

FIXTURES = Path(__file__).parent / "fixtures" / "mjc"
TZ = "Asia/Jerusalem"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _settings() -> MjcSettings:
    return MjcSettings(
        cron="22 7,19 * * *",
        venue_name="המרכז העולמי למורשת יהדות צפון אפריקה",
        address="המערבים 13",
        city="ירושלים",
        latitude=31.7768653,
        longitude=35.2212822,
    )


def _cards() -> dict[str, Card]:
    cards = parse_listing((FIXTURES / "events.html").read_text(encoding="utf-8"), _settings())
    return {c.title: c for c in cards}


def test_listing_keeps_one_day_posts() -> None:
    cards = _cards()
    titles = list(cards)
    assert "ערב חגיגי לכבוד התערוכה ״נחושת ואור בירושלים״" in titles
    assert "הילולה לרב דוד בן שמעון ״הצוף דבש״ זצוק״ל" in titles
    assert "תערוכת ״שורשים ומים״ — איתן זהבי" in titles  # a past opening: the scraper drops it by date
    # A weekday exhibition, a school programme and a weekly women's group have no one day.
    assert not any("תערוכת חנוכיות" in t or "גפ" in t or "קבוצת פיוט" in t for t in titles)
    assert not any(t.startswith("סיורי סליחות") for t in titles)  # a range of days (1.8.26-1.10.26)
    erev = cards["ערב חגיגי לכבוד התערוכה ״נחושת ואור בירושלים״"]
    assert (erev.day, erev.clock) == (date(2026, 12, 3), time(19, 0))
    assert erev.image and erev.image.startswith("https://storage.googleapis.com/")


def test_evening_at_the_centre() -> None:
    card = _cards()["ערב חגיגי לכבוד התערוכה ״נחושת ואור בירושלים״"]
    text = post_text((FIXTURES / "erev.html").read_text(encoding="utf-8"))
    event = build_event(card, text, _settings(), TZ)
    assert _iso(event.start_at) == "2026-12-03T19:00:00+02:00"
    assert (event.venue_name, event.city, event.latitude) == (
        "המרכז העולמי למורשת יהדות צפון אפריקה",
        "ירושלים",
        31.7768653,
    )
    assert event.extra["purchase_url"] == card.url
    assert "פרופ׳ שלום צבר" in (event.description or "")
    enriched = Enricher(get_settings().enrichment, TZ).enrich(event)
    assert enriched is not None and "piyut" in enriched.genres


def test_hilula_at_a_synagogue() -> None:
    card = _cards()["הילולה לרב דוד בן שמעון ״הצוף דבש״ זצוק״ל"]
    text = post_text((FIXTURES / "hilula.html").read_text(encoding="utf-8"))
    event = build_event(card, text, _settings(), TZ)
    assert event.venue_name == "בית הכנסת ״צוף דבש״"
    assert event.address == "רחוב פלוגות הכותל 15"
    assert event.latitude is None and event.city == "ירושלים"
    assert _iso(event.start_at) == "2026-11-26T14:30:00+02:00"  # the programme's first clock
