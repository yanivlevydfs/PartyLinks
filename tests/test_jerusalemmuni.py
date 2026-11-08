"""Jerusalem municipality parser tests against real pages (8 November 2026): the calendar's
GetEventsNew answer and three event pages (a ticketed show, a free weekly jam, an exhibition)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.parsing import zone
from partylinks.scrapers import jerusalemmuni as jm

FIXTURES = Path(__file__).parent / "fixtures" / "jerusalemmuni"
TZ = "Asia/Jerusalem"
S = jm.JerusalemMuniSettings(cron="0 0 * * *")


def page(name: str) -> jm.Page:
    return jm.parse_page((FIXTURES / f"{name}.html").read_text(encoding="utf-8"), S)


def cards() -> list[jm.Card]:
    return jm.parse_cards((FIXTURES / "listing.html").read_text(encoding="utf-8"), S.site_url, date(2026, 11, 8))


def test_listing_cards_are_dates_of_events() -> None:
    found = cards()
    assert len(found) == 40  # the answer repeats them in a list view: kept once
    tunnels = found[0]
    assert (
        tunnels.path == "/he/experience/allevents/walltunnels/?occ=126"
        and tunnels.page == "/he/experience/allevents/walltunnels/"
    )
    assert (tunnels.title, tunnels.day, tunnels.start, tunnels.end) == (
        "מנהרות הכותל | סיור",
        date(2026, 11, 8),
        "09:00",
        "21:00",
    )
    assert (
        tunnels.venue == "הכותל המערבי"
        and tunnels.image
        and tunnels.image.startswith("https://www.jerusalem.muni.il/media/")
    )
    assert not tunnels.free and next(c for c in found if "truestory" in c.path).free


def test_a_day_month_before_today_is_next_year() -> None:
    assert jm._day("08.11", date(2026, 11, 8)) == date(2026, 11, 8)
    assert jm._day("03.01", date(2026, 11, 8)) == date(2027, 1, 3)
    assert jm._day("no date", date(2026, 11, 8)) is None


def test_ticketed_show_page_gives_pin_prices_and_seller() -> None:
    info = page("cinderellashow")
    assert info.venue == "תיאטרון ירושלים" and (info.latitude, info.longitude) == (31.768523, 35.216497)
    assert info.purchase_url and info.purchase_url.startswith("https://www.tickets.bimot.co.il/event/")
    assert [(t.name, t.price) for t in info.tiers] == [("מחיר מלא", Decimal(109)), ("חברי מועדון ירושלמי", Decimal(64))]
    assert (
        info.description
        and info.description.startswith("סינדרלה חוזרת לבמה")
        and "לרכישת כרטיסים" not in info.description
    )


def test_free_jam_and_exhibition_read_more_link_is_no_ticket() -> None:
    jam = page("jamsession")
    assert jam.free and [t.is_free for t in jam.tiers] == [True] and jam.purchase_url is None  # wa.me is a chat
    exhibition = page("thepromise")
    assert exhibition.purchase_url is None  # "לקריאה נוספת" on the curator's site


def test_event_crosses_midnight_and_gets_site_genres() -> None:
    card = jm.Card(path="/he/experience/allevents/jamsession/?occ=0", title="ג'אם סשן", day=date(2026, 11, 9),
                   start="22:00", end="02:00", venue="Besarabia", teaser=None, image=None, free=True,
                   genres=["מסיבות ומופעים", "מוסיקה"])  # fmt: skip
    event = jm.build_event(card, page("jamsession"), S, TZ)
    assert event is not None
    assert event.source_event_id == "jamsession-202611092200"
    assert event.start_at == datetime(2026, 11, 9, 22, 0, tzinfo=zone(TZ))
    assert event.end_at == datetime(2026, 11, 10, 2, 0, tzinfo=zone(TZ))
    assert (event.latitude, event.city, event.min_price) == (31.78150031847875, "ירושלים", Decimal(0))
    settings = Settings()
    assert "live_show" in Enricher(settings.enrichment, settings.timezone)._detect_genres(event)
    # A date at another hall than the page's: no pin, the hall's name goes to geocoding.
    moved = jm.build_event(jm.Card(**{**card.__dict__, "venue": "מרכז אחר"}), page("jamsession"), S, TZ)
    assert moved is not None and moved.latitude is None and moved.venue_name == "מרכז אחר"


def test_mizrahi_surname_and_side_are_no_genre() -> None:
    settings = Settings()
    detect = Enricher(settings.enrichment, settings.timezone)._detect_genres
    card = jm.Card(path="/he/experience/allevents/x/?occ=0", title="ההבטחה | תערוכה", day=date(2026, 11, 9),
                   start="10:00", end=None, venue=None, teaser=None, image=None, free=True)  # fmt: skip
    plain = jm.Page(description="אוצרות: אסף כהן ויצחק מזרחי. סיור בקרדו המזרחי.", venue=None, latitude=None,
                    longitude=None, purchase_url=None, tiers=[], free=True)  # fmt: skip
    event = jm.build_event(card, plain, S, TZ)
    assert event is not None and "mizrahi" not in detect(event)
    sung = jm.build_event(card, jm.Page(**{**plain.__dict__, "description": "ערב עם זמר מזרחי ותזמורת מזרחית"}), S, TZ)
    assert sung is not None and "mizrahi" in detect(sung)
    declared = jm.build_event(jm.Card(**{**card.__dict__, "genres": ["מזרחי"]}), plain, S, TZ)
    assert declared is not None and "mizrahi" in detect(declared)
