"""Season holidays (search.GENRE_HOLIDAYS: Christmas, Ramadan, the Eids): their own events through the season,
the dates of the calendars we do not compute (src/partylinks/holiday_dates.json), the holiday genres' words."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from partylinks.api import create_app
from partylinks.config import get_settings
from partylinks.enrichment import Enricher
from partylinks.holiday_pages.core import upcoming
from partylinks.models import ScrapedEvent
from partylinks.search import DATED, _next_holiday, date_ranges
from tests.test_storage_api import event, seed, settings_for

TZ = ZoneInfo("Asia/Jerusalem")


def test_dates_from_the_fetched_calendar_and_the_hebrew_one() -> None:
    assert _next_holiday(date(2026, 11, 28), "christmas") == (date(2026, 12, 1), date(2027, 1, 20))
    assert _next_holiday(date(2026, 11, 28), "ramadan") == (date(2027, 2, 8), date(2027, 3, 9))
    assert _next_holiday(date(2026, 11, 28), "mimouna") == (
        date(2027, 4, 28),
        date(2027, 4, 29),
    )  # the night Pesach ends
    assert _next_holiday(date(2026, 11, 28), "jerusalem_day") == (date(2027, 6, 3), date(2027, 6, 4))
    assert _next_holiday(date(2026, 11, 28), "sigd") == (date(2027, 11, 29), date(2027, 11, 29))
    assert len([s for s in DATED["ramadan"] if s[0].year == 2030]) == 2  # two Ramadans in 2030: both kept


def test_a_season_holiday_is_not_a_date_word_in_search() -> None:
    words = date_ranges(date(2026, 11, 28))
    assert "חנוכה" in words and "סילבסטר" in words
    assert "כריסמס" not in words and "רמדאן" not in words  # these find their events (a genre), not all of December


def test_the_holiday_genres_and_their_traps() -> None:
    s = get_settings()
    enricher = Enricher(s.enrichment, s.timezone)
    keys = {"christmas", "christmas_markets", "hanukkah", "purim", "new_year_eve", "ramadan", "eid_al_adha"}

    def genres(title: str) -> set[str]:
        e = ScrapedEvent(source="t", source_event_id=title, url="https://x.example/e", title=title,
                         start_at=datetime(2026, 12, 20, 20, tzinfo=TZ))  # fmt: skip
        enriched = enricher.enrich(e)
        return set(enriched.genres if enriched else []) & keys

    assert genres("Christmas Market Ramada – Friday Entry") == {"christmas", "christmas_markets"}
    assert genres("שוק חג המולד בנצרת") == {"christmas", "christmas_markets"}
    assert genres("عيد الميلاد المجيد") == {"christmas"}
    assert genres("מסיבת סילבסטר בגגרין") == {"new_year_eve"} and genres("Новый год в Хайфе") == {"new_year_eve"}
    assert genres("פסטיגל 2026") == {"hanukkah"} and genres("מסיבת פורים ענקית") == {"purim"}
    assert genres("איפטאר רמדאן ביפו") == {"ramadan"}
    # Not holidays: a housewarming, a birthday, Santa Cruz, the Ramada hotel, Santana.
    for title in ["חנוכת הבית של הקפה", "عيد ميلاد سعيد", "סנטה קרוז מופע", "Ramada Hotel brunch", "Santana tribute"]:
        assert genres(title) == set(), title


def test_the_christmas_page_lists_christmas_events_only(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    today = datetime.now(TZ).date()
    xmas = next(h for h in upcoming(settings, today) if h.key == "christmas")
    assert xmas.genre == "christmas"
    night = datetime.combine(max(xmas.start, today) + timedelta(days=2), time(20), TZ)
    seed(
        settings,
        [
            event(f"m{i}", night + timedelta(hours=i), title=f"שוק חג המולד {i}", source_genres=["christmas"])
            for i in range(3)
        ]
        + [event("techno", night, title="Techno night", source_genres=["techno"])],
    )
    with TestClient(create_app(settings, start_scheduler=False)) as client:
        page = client.get("/holiday/christmas")
        assert page.status_code == 200 and "שוק חג המולד 0" in page.text and "Techno night" not in page.text
        assert "/holidays/christmas-og.webp" in page.text  # its own drawn picture
        listed = {x["key"]: x for x in client.get("/holidays/list").json()["holidays"]}
        assert listed["christmas"]["genre"] == "christmas" and listed["christmas"]["events"] == 3


def test_a_fetched_holiday_with_no_dates_left_is_skipped(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from partylinks import search
    from partylinks.config import Settings
    from partylinks.home_rails.config import HomeRail

    monkeypatch.setitem(search.DATED, "ramadan", [(date(2020, 4, 23), date(2020, 5, 23))])  # only past dates known
    today = date(2026, 11, 28)
    assert search._next_holiday(today, "ramadan") is None
    assert "ramadan" not in {h.key for h in upcoming(Settings(), today)}
    window = HomeRail(key="r", holiday="ramadan").window(today)
    assert window is not None and window[1] < today  # an empty window: the row hides
    search.date_ranges.cache_clear()
    try:
        assert "מימונה" in search.date_ranges(today)  # the others still work
    finally:
        search.date_ranges.cache_clear()


def test_towns_take_their_name_in_the_states_list_of_localities() -> None:
    s = get_settings()
    enricher = Enricher(s.enrichment, s.timezone)
    town = lambda name: enricher.canonical_city(name, None)
    # GeoNames' first names were "אל-בוקיעה", "מפשטה", "בית כרם": the official names (localities.json) instead.
    assert town("אל-בוקיעה") == town("פקיעין") == "פקיעין (בוקייעה)"
    assert town("גוש חלב") == town("ג'ש") == "ג'ש (גוש חלב)"
    assert town("מפשטה") == "פסוטה" and town("מיעיליא") == "מעיליא" and town("סכנין") == "סח'נין"
    assert town("Umm el Fahm") == "אום אל-פחם" and town("קריית שמונה") == "קרית שמונה"
    assert town("ג'ת") == "ג'ת"  # the geresh counts: not kibbutz גת
