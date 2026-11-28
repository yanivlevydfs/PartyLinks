"""Western Galilee tourism (bgalil.co.il): its events board and item pages, trimmed real pages (28.11.2026)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.bgalil import BgalilSettings, Card, find_dates, parse_item, parse_listing, parse_page

FIXTURES = Path(__file__).parent / "fixtures" / "bgalil"
TZ = "Asia/Jerusalem"
TODAY = date(2026, 11, 28)
SETTINGS = BgalilSettings(cron="30 8,20 * * *")


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _card(href: str, labels: list[str] | None = None) -> Card:
    card = next(c for c in parse_listing(_read("events.html"), SETTINGS.site_url) if c.href == href)
    card.labels = labels or []
    return card


def test_listing_cards_keep_items_and_drop_links_off_the_site() -> None:
    cards = parse_listing(_read("events.html"), SETTINGS.site_url)
    hrefs = [c.href for c in cards]
    assert "maruan-event" in hrefs and "Novi-Good-AKKO" in hrefs
    assert not any(h.startswith("http") or "op=category" in h for h in hrefs)  # an Instagram post, a section
    maruan = next(c for c in cards if c.href == "maruan-event")
    assert maruan.item_id == "5389" and maruan.title == "רגעי כריסמס בגליל 18/19.12.26"


def test_dates_in_every_spelling() -> None:
    assert find_dates("רגעי כריסמס בגליל 18/19.12.26", TODAY) == [date(2026, 12, 18), date(2026, 12, 19)]
    assert find_dates("סיורי עששיות, בחנוכה 06-10.12.26", TODAY)[-1] == date(2026, 12, 10)
    assert find_dates("9-10 לדצמבר - פסטיבל", TODAY) == [date(2026, 12, 9), date(2026, 12, 10)]
    assert find_dates("4/12 - חנוכה במוזיאון", TODAY) == [date(2026, 12, 4)]
    assert find_dates('"מיסה די גלוריה"02.01.27 המקהלה', TODAY) == [date(2027, 1, 2)]
    assert find_dates("בין התאריכים 08/12/-/11/12", TODAY) == [date(2026, 12, d) for d in (8, 9, 10, 11)]
    assert len(find_dates("משבת 05/12/2026 עד שבת 12/12/2026, כל יום", TODAY)) == 8
    # A date without a year falls in the coming months: January is next year's.
    assert find_dates("16.1 - ערב", TODAY) == [date(2027, 1, 16)]
    # In a page's text a bare day.month needs a date word: a rating and opening hours are none.
    assert find_dates("דירוג שיא של 9.8 בבוקינג, פתוח 24/7", TODAY, cued=True) == []
    assert find_dates("תאריכים: 5.12 | 6.12", TODAY, cued=True) == [date(2026, 12, 5), date(2026, 12, 6)]


def test_christmas_tour_in_miilya() -> None:
    page = parse_page(_read("maruan.html"), SETTINGS.site_url)
    assert page.address == "בכפר מעליא" and page.latitude is not None and abs(page.latitude - 33.0268) < 0.001
    assert page.booking == "https://itgacard.com/marwan-ewida"
    first, second = parse_item(_card("maruan-event", ["אטרקציות"]), page, SETTINGS, TZ, TODAY)
    assert first.start_at is not None and first.start_at.isoformat() == "2026-12-18T17:00:00+02:00"
    assert first.end_at is not None and first.end_at.isoformat() == "2026-12-18T19:00:00+02:00"
    assert second.start_at is not None and second.start_at.date() == date(2026, 12, 19)
    assert first.source_event_id == "5389-2026-12-18" and first.source_genres == ["אטרקציות"]
    assert first.images[0].startswith("https://www.bgalil.co.il/files/places/item/thumbsrc/")
    assert [str(t.price) for t in first.tickets] == ["50", "100"]
    enriched = Enricher(Settings().enrichment, TZ).enrich(first)
    assert enriched is not None and "christmas" in enriched.genres and enriched.city


def test_knights_halls_hanukkah_and_christmas_dates_from_the_text() -> None:
    page = parse_page(_read("knights.html"), SETTINGS.site_url)
    events = parse_item(_card("Novi-Good-AKKO"), page, SETTINGS, TZ, TODAY)
    days = [e.start_at.date() for e in events if e.start_at]
    assert days[0] == date(2026, 12, 8) and days[-1] == date(2026, 12, 21) and len(days) == 10
    assert {e.city for e in events} == {"עכו"}
    assert events[0].extra["purchase_url"] == "https://www.akko.org.il/attractions/"


def test_daily_tour_with_prices() -> None:
    page = parse_page(_read("regba.html"), SETTINGS.site_url)
    events = parse_item(_card("shavuot-amat-hamaim"), page, SETTINGS, TZ, TODAY)
    assert len(events) == 8 and all(e.start_at and e.start_at.hour == 11 for e in events)
    assert events[0].city == "רגבה" and {str(t.price) for t in events[0].tickets} >= {"33", "43"}


def test_exhibition_runs_from_opening_to_closing() -> None:
    page = parse_page(_read("kabri.html"), SETTINGS.site_url)
    events = parse_item(_card("kabri-gallery"), page, SETTINGS, TZ, TODAY)
    runs = [(e.start_at.date(), e.end_at.date()) for e in events if e.start_at and e.end_at]
    assert (date(2026, 10, 17), date(2027, 1, 16)) in runs


def test_undated_business_is_no_event() -> None:
    page = parse_page(_read("hanita_cafe.html"), SETTINGS.site_url)
    assert parse_item(_card("Backyard-hanita"), page, SETTINGS, TZ, TODAY) == []
