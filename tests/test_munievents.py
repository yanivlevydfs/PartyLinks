"""A.S. Bina municipal CMS: the JSON feed, every listing layout and event pages to ScrapedEvent."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from partylinks.models import TierStatus
from partylinks.scrapers.munievents import Card, MuniSite, build_event, parse_categories, parse_feed, parse_listing

TZ = "Asia/Jerusalem"
TODAY = date(2026, 10, 20)
FIXTURES = Path(__file__).parent / "fixtures/munievents"
ROSH = MuniSite(url="https://www.rosh-haayin.muni.il", city="ראש העין")
KFAR = MuniSite(url="https://www.kfar-saba.muni.il", city="כפר סבא")


def _read(name: str) -> str:
    return (FIXTURES / name).read_text("utf-8")


def _cards() -> dict[str, Card]:
    return {c.event_id: c for c in parse_listing(_read("rosh_haayin_events.html"), ROSH.url, TZ, TODAY)}


def test_listing_keeps_own_cards_only() -> None:
    cards = _cards()
    assert list(cards) == ["1827", "1844", "1875", "1830", "1876"]  # the two Smarticket cards are left out


def test_sold_out_paid_event() -> None:
    e = build_event(_cards()["1830"], _read("rosh_haayin_1830.html"), ROSH, TZ)
    assert e is not None and e.source_event_id == "rosh-haayin:1830" and e.title == "סדנת תכשיטנות"
    assert e.start_at is not None and e.start_at.isoformat() == "2026-11-01T19:30:00+02:00" and e.end_at is None
    assert (e.venue_name, e.address, e.city) == ("מרכז פיס תורני", "רח' המתמיד 23 פינת יהודה הלוי", "ראש העין")
    assert e.min_price == Decimal(40) and e.is_sold_out and e.tickets[0].status is TierStatus.SOLD_OUT
    assert e.source_genres == ["נשים", "סדנה"] and e.description and e.description.startswith("בואי להתפנק")
    assert e.image_url == "https://www.rosh-haayin.muni.il/uploads/n/1791380822.2495.jpg"  # full size, not c-480


def test_on_sale_with_remaining_and_end_time() -> None:
    e = build_event(_cards()["1875"], _read("rosh_haayin_1875.html"), ROSH, TZ)
    assert e is not None and e.end_at is not None and e.end_at.isoformat() == "2026-10-25T20:30:00+02:00"
    assert e.tickets[0].price == Decimal(10) and e.tickets[0].remaining == 5 and not e.is_sold_out


def test_no_tickets_row_season_and_undated() -> None:
    cards = _cards()
    e = build_event(cards["1876"], None, ROSH, TZ)
    assert e is not None and e.tickets == [] and e.venue_name == 'מתנ"ס גבעת הסלעים' and e.address is None
    assert "c-480" in (e.image_url or "")  # no page: the card's thumbnail
    season = build_event(cards["1844"], None, ROSH, TZ)
    assert season is not None and season.start_at is not None
    assert season.start_at.isoformat() == "2026-09-02T18:00:00+03:00"  # its first session
    assert build_event(cards["1827"], None, ROSH, TZ) is None  # "ללא תאריך"


def test_kfar_saba_same_engine() -> None:
    card = parse_listing(_read("kfar_saba_events.html"), KFAR.url, TZ, TODAY)[0]
    e = build_event(card, _read("kfar_saba_detail.html"), KFAR, TZ)
    assert e is not None and e.source_event_id == "kfar-saba:6923" and e.city == "כפר סבא"
    assert (e.venue_name, e.address) == ("ארט קיימא", 'רחוב אז"ר 45') and e.tickets == []
    assert e.start_at is not None and e.start_at.isoformat() == "2026-10-23T09:30:00+03:00"
    assert e.description and "פוטותרפיה" in e.description


def _feed(name: str, site: MuniSite, categories: dict[str, str] | None = None, today: date = TODAY) -> dict[str, Card]:
    return {c.event_id: c for c in parse_feed(json.loads(_read(name)), site.url, categories or {}, TZ, today)}


def test_feed_keeps_upcoming_own_events_once() -> None:
    cards = _feed("kfar_saba_feed.json", KFAR, today=date(2026, 10, 22))
    assert set(cards) == {"6389", "6914", "6902", "2"}  # past, cancelled and Smarticket rows left out
    weekly = cards["6389"]  # a row per date: kept at its next one
    assert weekly.start is not None and weekly.start.isoformat() == "2026-10-22T08:00:00+03:00" and weekly.end is None
    assert weekly.tags == ["ספורט"] and weekly.place == "ברחבי העיר" and weekly.url.endswith("/events/6389/")
    season = cards["2"]  # began in September, runs to June: still listed, at its first session
    assert season.start is not None and season.start.isoformat() == "2026-09-02T18:00:00+03:00"
    assert season.end is not None and season.end.isoformat() == "2027-06-30T21:00:00+03:00"
    assert season.tags == ['מתנ"ס'] and season.place == 'מרכז "הדר"'  # double-escaped feed text


def test_feed_event_with_page() -> None:
    card = _feed("kfar_saba_feed.json", KFAR, today=date(2026, 10, 22))["6914"]
    e = build_event(card, _read("kfar_saba_detail.html"), KFAR, TZ)
    assert e is not None and e.source_event_id == "kfar-saba:6914" and e.city == "כפר סבא"
    assert (e.venue_name, e.address) == ("הבית לגיל הרך", "רחוב דוד המלך 7")
    assert e.source_genres == ["ילדים והגיל הרך"] and e.description and e.organizer == "עיריית כפר סבא"


def test_old_feed_names_categories_from_the_listing() -> None:
    soreq = MuniSite(url="https://www.ma-soreq.org.il", city="מועצה אזורית נחל שורק", regional=True)
    cards = _feed("ma_soreq_feed.json", soreq, parse_categories(_read("ma_soreq_events.html")))
    assert list(cards) == ["1074", "1076"]  # the Tickchak row is left out
    assert cards["1076"].tags == ["אירועי הספרייה"] and cards["1076"].place is None


def test_old_feed_utc_stamps_are_local_times() -> None:
    arava = MuniSite(url="https://www.arava.co.il", city="מועצה אזורית הערבה התיכונה", regional=True)
    cards = _feed("arava_feed.json", arava)
    assert list(cards) == ["2470", "2345"]  # 2016's festival is long over
    assert cards["2470"].start is not None and cards["2470"].start.isoformat() == "2026-10-27T20:30:00+02:00"
    festival = cards["2345"]  # malformed end "2026-11-14T2026-11-14+00:00": its day still counts
    assert festival.last_day == date(2026, 11, 14) and festival.end is None
    e = build_event(festival, None, arava, TZ)
    assert e is not None and e.source_genres == ["קהילה"]  # filed under no category: the site's default
    assert e.city == "מועצה אזורית הערבה התיכונה" and e.organizer == "מועצה אזורית הערבה התיכונה"


def test_listing_layouts_without_feed() -> None:
    day = date(2026, 9, 25)
    herzliya = parse_listing(_read("herzliya_events.html"), "https://www.herzliya.muni.il", TZ, day)
    assert [c.event_id for c in herzliya] == ["13814", "13884"]  # the matnasim card links to another site
    assert herzliya[0].start is not None and herzliya[0].start.isoformat() == "2026-10-23T09:00:00+03:00"
    assert herzliya[1].place == "מוזיאון בית ראשונים, הנדיב 8, הרצליה" and herzliya[1].tags == ["מוזיאון בית ראשונים"]
    shmuel = parse_listing(_read("givat_shmuel_events.html"), "https://www.givat-shmuel.muni.il", TZ, day)
    assert [(c.title, c.start.isoformat() if c.start else None, c.place, c.tags) for c in shmuel][1] == (
        'שעת סיפור: "סיפורי חתולים"',
        "2026-10-12T16:30:00+03:00",
        "בספריה",
        ["אירועי העיריה"],
    )  # "12 אוקטובר": no year given
    late = parse_listing(_read("givat_shmuel_events.html"), "https://www.givat-shmuel.muni.il", TZ, date(2026, 10, 14))
    assert [c.event_id for c in late] == ["859"]  # over by then
    eshkol = parse_listing(_read("eshkol_events.html"), "https://www.eshkol.info", TZ, day)
    assert [(c.event_id, c.place) for c in eshkol] == [("1652", "פריגן"), ("1653", None), ("1650", "יער חרבות ברזל")]
    tikva = parse_listing(_read("ganey_tikva_events.html"), "https://www.ganeytikva.org.il", TZ, day)
    assert tikva[1].start is not None and tikva[1].start.isoformat() == "2026-10-24T18:30:00+03:00"


def test_page_fills_date_hour_and_place() -> None:
    eshkol = MuniSite(url="https://www.eshkol.info", city="מועצה אזורית אשכול", regional=True)
    card = parse_listing(_read("eshkol_events.html"), eshkol.url, TZ, date(2026, 9, 25))[0]
    e = build_event(card, _read("eshkol_detail.html"), eshkol, TZ)
    assert e is not None and e.start_at is not None and e.start_at.isoformat() == "2026-10-01T00:00:00+03:00"
    assert e.end_at is None  # the page gives no hour, so its "01:00" end means nothing
    assert e.description and "מירוץ" in e.description  # plain paragraphs, no rich-content block
    eilot = MuniSite(url="https://www.eilot.org.il", city="מועצה אזורית חבל אילות", regional=True)
    unlisted = Card(
        url="https://www.eilot.org.il/events/2766/", event_id="2766", title="אלון עדר", start=None, end=None
    )
    e = build_event(unlisted, _read("eilot_detail.html"), eilot, TZ)
    assert e is not None and e.start_at is not None and e.start_at.isoformat() == "2026-10-22T17:30:00+03:00"
    assert (e.venue_name, e.address) == ("בית האומנויות", "נאות סמדר")  # add-to-calendar location
    assert e.city == "מועצה אזורית חבל אילות"  # a kibbutz too small to be a known locality
    assert e.description and e.description.startswith("קשה לדמיין")


def test_regional_council_city_is_the_named_locality() -> None:
    arava = MuniSite(url="https://www.arava.co.il", city="מועצה אזורית הערבה התיכונה", regional=True)
    card = Card(
        url="https://www.arava.co.il/events/9/",
        event_id="9",
        title="t",
        start=None,
        end=None,
        place="מרכז קהילה - עין יהב",
    )
    card.start = _feed("arava_feed.json", arava)["2470"].start
    e = build_event(card, None, arava, TZ)
    assert e is not None and (e.venue_name, e.address, e.city) == ("מרכז קהילה", "עין יהב", "עין יהב")
