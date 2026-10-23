"""Holon municipality: the events board page to one ScrapedEvent per row."""

from __future__ import annotations

from pathlib import Path

from selectolax.lexbor import LexborHTMLParser

from partylinks.scrapers.holon import HolonSettings, parse_row

TZ = "Asia/Jerusalem"
PAGE = LexborHTMLParser((Path(__file__).parent / "fixtures/holon/allevents.html").read_text("utf-8"))
SETTINGS = HolonSettings(
    cron="9 4,16 * * *", listing_url="https://www.holon.muni.il/Havingfun/Pages/allevents.aspx", city="חולון"
)
EVENTS = {e.source_event_id: e for e in (parse_row(row, PAGE, SETTINGS, TZ) for row in PAGE.css("tr.rowMain")) if e}


def test_one_off_show_with_panel_details() -> None:
    assert len(EVENTS) == 6
    story = EVENTS["9206"]  # library story hour, on no ticket site
    assert story.title == "הדייג ודג הזהב" and story.venue_name == "ספריית נבון" and story.city == "חולון"
    assert story.start_at is not None and story.start_at.isoformat() == "2026-10-28T17:00:00+02:00"
    assert story.end_at is not None and story.end_at.isoformat() == "2026-10-28T17:45:00+02:00"
    assert str(story.url) == "https://www.holon.muni.il/Havingfun/Lists/List/CustomDispForm.aspx?ID=9206"
    assert story.images[0].startswith("https://www.holon.muni.il/Havingfun/DocLib1/Library/")
    assert story.event_types == ["שעת סיפור"] and story.extra == {"audience": "ילדים", "phone": "03-5021555"}
    assert story.description and not story.description.startswith("תיאור")  # panel heading dropped
    theatre = EVENTS["9196"]
    assert theatre.venue_name == "תיאטרון חולון" and theatre.event_types == ["הצגות ומופעים"]  # "כללי" dropped


def test_series_keep_first_date_without_their_last_end() -> None:
    # Exhibition 2024-2030: the hidden " 00:00" must not be read as a 4-digit year.
    museum = EVENTS["7426"]
    assert museum.start_at is not None and museum.start_at.isoformat() == "2024-12-22T00:00:00+02:00"
    lectures = EVENTS["9176"]  # weekly until April: the last end is not this session's
    assert lectures.start_at is not None and lectures.start_at.isoformat() == "2026-12-03T11:00:00+02:00"
    assert museum.end_at is None and lectures.end_at is None
    assert EVENTS["6096"].venue_name is None  # online class: no place
