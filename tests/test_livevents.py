"""Live Events parser test against the start of a real month table (June 2027)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from partylinks.scrapers import livevents

TABLE = (Path(__file__).parent / "fixtures" / "livevents" / "june.html").read_text(encoding="utf-8")
CALENDAR = "https://livevents.co.il/events/"


def test_livevents_rows() -> None:
    events = livevents.parse_table(TABLE, CALENDAR, "Asia/Jerusalem")
    assert len(events) == 5 + 11  # five single-date rows, Oasis in Manchester on eleven dates
    rod = events[0]
    assert (rod.title, rod.city, rod.country, rod.artists) == ("רוד סטיוארט", "ברלין", "DE", ["רוד סטיוארט"])
    assert rod.start_at == datetime(2027, 6, 1, tzinfo=ZoneInfo("Asia/Jerusalem"))
    assert (str(rod.url), rod.currency, rod.min_price, rod.extra) == (
        "https://livevents.co.il/package/rod-stewart-ber-gold/",
        "EUR",
        Decimal(949),
        {"package": True},
    )
    quote = events[1]  # "request a quote": no price, the calendar is the link
    assert (quote.title, quote.city, quote.country, str(quote.url), quote.min_price) == (
        "ניקלבק",
        "מנצ'סטר",
        "GB",
        CALENDAR,
        None,
    )
    oasis = [e for e in events if e.title == "אואזיס"]
    assert len({e.source_event_id for e in oasis}) == 11 and {(e.currency, e.min_price) for e in oasis} == {
        ("GBP", Decimal(1449))
    }


def test_artist_card_gives_the_picture() -> None:
    first = livevents.parse_table(TABLE, CALENDAR, "Asia/Jerusalem")[0]
    cards = livevents.artist_images(
        f'<a class="sitem" href="/a"><img class="bimg" src="https://livevents.co.il/u/a.png">'
        f'<div class="artist">{first.title}</div></a>'
    )
    event = livevents.parse_table(TABLE, CALENDAR, "Asia/Jerusalem", cards)[0]
    assert event.images == ["https://livevents.co.il/u/a.png"]


def test_sold_out_status() -> None:
    """Oasis's rows (its tour page, the calendar's markup, Nov 2026): "SOLD OUT" in the status cell is sold out."""
    tour = (Path(__file__).parent / "fixtures" / "livevents" / "tour_oasis.html").read_text(encoding="utf-8")
    events = livevents.parse_table(tour, CALENDAR, "Asia/Jerusalem")
    munich = next(e for e in events if e.city == "מינכן")
    assert (str(munich.url), munich.currency, munich.min_price) == (
        "https://livevents.co.il/package/oasis-muc-gold/",
        "EUR",
        Decimal(1299),
    )
    rome = [e for e in events if e.city == "רומא"]
    assert rome and all(e.is_sold_out and e.min_price is None for e in rome)
    assert not munich.is_sold_out


def test_sport_tables() -> None:
    """The sport section's tables (Nov 2026): football matches and Formula 1 weekends, no artist, a declared genre."""
    fixtures = Path(__file__).parent / "fixtures" / "livevents"
    matches_url = "https://livevents.co.il/matches/"
    matches = livevents.parse_table(
        (fixtures / "matches.html").read_text(encoding="utf-8"), matches_url, "Asia/Jerusalem", genre="כדורגל"
    )
    assert len(matches) == 8  # November 2026's eight match dates
    city = matches[0]
    assert (city.title, city.city, city.country, city.artists, city.source_genres, str(city.url)) == (
        "צ'מפיונס: מנצ'סטר סיטי - נאפולי",
        "מנצ'סטר",
        "GB",
        [],
        ["כדורגל"],
        matches_url,
    )
    assert city.start_at == datetime(2026, 11, 24, tzinfo=ZoneInfo("Asia/Jerusalem"))
    assert city.extra == {"day": "מועד סופי"}
    races = livevents.parse_table(
        (fixtures / "formula1.html").read_text(encoding="utf-8"),
        "https://livevents.co.il/formula1/",
        "Asia/Jerusalem",
        genre="motorsport",
    )
    assert len(races) == 27  # nine race weekends, three days each
    abu = [e for e in races if e.title == "פורמולה 1 אבו דאבי"]
    assert [e.extra["day"] for e in abu] == ["חימום", "חימום ודירוג", "יום המירוץ"]
    assert {(str(e.url), e.currency, e.min_price, e.country) for e in abu} == {
        ("https://livevents.co.il/race/formula-1-abu-dhabi/", "USD", Decimal(2499), "AE")
    }
    assert all(e.country != "ZZ" for e in races)
