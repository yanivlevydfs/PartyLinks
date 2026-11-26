"""Kaspit: the agency's show calendar page, trimmed real answer (27.11.2026)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.kaspit import KaspitSettings, city_of, parse_listing

PAGE = (Path(__file__).parent / "fixtures" / "kaspit" / "listing.html").read_text(encoding="utf-8")
TZ = "Asia/Jerusalem"
URL = "https://kaspit-art.co.il/%d7%94%d7%95%d7%a4%d7%a2%d7%95%d7%aa/"
SETTINGS = KaspitSettings(
    cron="17 5,17 * * *",
    venue_cities={"בארבי": "תל אביב-יפו", "האנגר 11": "תל אביב-יפו"},
    known_cities=["אילת", "ירושלים"],
)
TODAY = date(2026, 11, 27)


def _events():
    return parse_listing(PAGE, URL, SETTINGS, TZ, TODAY)


def test_first_show() -> None:
    first = _events()[0]
    assert first.source_event_id == "4942"
    assert first.title == "אהוד בנאי" and first.artists == ["אהוד בנאי"]
    # 26.11 is yesterday: still this year (today's late shows end after midnight), not next year.
    assert first.start_at is not None and first.start_at.isoformat() == "2026-11-26T22:00:00+02:00"
    assert first.venue_name == "בארבי נמל יפו" and first.city == "תל אביב-יפו"
    assert first.extra["purchase_url"] == "https://barby.co.il/show/5505"
    assert first.images == ["https://kaspit-art.co.il/wp-content/uploads/2023/01/בנאי.jpg"]  # not the 90x90 thumbnail
    assert str(first.url).startswith("https://kaspit-art.co.il/artists/")


def test_january_is_next_year_and_the_note_joins_the_title() -> None:
    festival = next(e for e in _events() if "פסטיבל הפסנתר" in e.title)
    assert festival.start_at is not None and festival.start_at.year == 2027
    assert festival.title == "אהוד בנאי - פסטיבל הפסנתר"
    assert festival.city == "אילת"
    feb = [e for e in _events() if e.start_at is not None and e.start_at.month == 2]
    assert feb and all(e.start_at is not None and e.start_at.year == 2027 for e in feb)


def test_city_of() -> None:
    assert city_of("נוקטורנו לייב - ירושלים", SETTINGS) == "ירושלים"
    assert city_of("האנגר 11", SETTINGS) == "תל אביב-יפו"
    assert city_of("מקום לא ידוע", SETTINGS) is None
    assert city_of(None, SETTINGS) is None


def test_enrichment_gives_site_genres() -> None:
    enriched = Enricher(Settings().enrichment, TZ).enrich(_events()[0])
    assert enriched is not None and enriched.genres
