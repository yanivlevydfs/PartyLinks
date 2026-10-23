"""Habama parser test against five rows of a real day page (24/10/2026, fetched October 2026)."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from partylinks.scrapers import habama

PAGE = (Path(__file__).parent / "fixtures" / "habama" / "day.html").read_text(encoding="utf-8")
DAY = date(2026, 10, 24)
TZ = "Asia/Jerusalem"


def test_habama_day_rows() -> None:
    streetcar, zinger, decadance, holon, ashdod = habama.parse_day(PAGE, DAY, TZ)
    assert (streetcar.source_event_id, streetcar.title, streetcar.venue_name, streetcar.city) == (
        "20805-202610241700",
        "חשמלית ושמה תשוקה - תיאטרון הקאמרי",
        "תיאטרון הקאמרי החדש",
        None,
    )
    assert streetcar.start_at == datetime(2026, 10, 24, 17, 0, tzinfo=ZoneInfo(TZ))
    assert str(streetcar.url) == "https://www.habama.co.il/Pages/Event.aspx?Subj=1&Area=1&EventId=20805"
    assert (streetcar.description, streetcar.extra) == ("דרמה מאת טנסי ויליאמס", {"show_id": "20805"})
    # The category heads its group only; the rows below inherit it.
    assert [e.source_genres for e in (streetcar, zinger, decadance, holon, ashdod)] == [
        ["תיאטרון"],
        ["תיאטרון"],
        ["מחול"],
        ["בידור"],
        ["בידור"],
    ]
    # "hall, city": the last comma part is the city.
    assert (decadance.venue_name, decadance.city) == ("אודיטוריום אריסון", "תל אביב")
    assert (ashdod.venue_name, ashdod.city) == ("בית יד לבנים, בית החייל", "אשדוד")


def test_habama_show_under_two_categories() -> None:
    # The same performance listed again under another category is one event with both genres.
    page = PAGE + PAGE.replace(">תיאטרון</td>", ">קלאסי</td>")
    events = habama.parse_day(page, DAY, TZ)
    assert len(events) == 5
    assert events[0].source_genres == ["תיאטרון", "קלאסי"]


def test_habama_downlevel_render() -> None:
    # A plain "Mozilla/5.0" User-Agent gets the rows without their height style.
    page = PAGE.replace("height:14px;padding-right:4px;", "padding-right:4px;")
    assert [e.source_event_id for e in habama.parse_day(page, DAY, TZ)] == [
        e.source_event_id for e in habama.parse_day(PAGE, DAY, TZ)
    ]
