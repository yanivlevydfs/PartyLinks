from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from partylinks.scrapers.standupist import StandupistSettings, find_city, parse_event, parse_sold_out, tag_slugs

TZ = "Asia/Jerusalem"
SETTINGS = StandupistSettings(cron="0 0 * * *", venue_names={"בא BE הוד השרון": "הבאBE בר"})

ROW = {
    "id": 87555,
    "post_status": "publish",
    "event_status": "scheduled",
    "name": "משה אשכנזי - באבי בר הוד השרון",
    "permalink": "https://standupist.co.il/events/300127/var/ri-.l-L1",
    "start": 1801422900,
    "end": 1801427400,
    "details": "<p>מופע סטנדאפ חדש</p>",
    "event_subtitle": "אל תפספסו",
    "all_day_event": "no",
    "image_url": "https://standupist.co.il/wp-content/uploads/2026/07/Moshe.jpg",
    "location_name": "בא BE הוד השרון",
    "location_address": "הנגר 8 הוד השרון",
    "location_lat": "32.13018511272441",
    "location_lon": "34.893672471164706",
    "event_type": {"171": "הופעה מלאה"},
    "event_type_3": {"506": "הופעות בתשלום"},
    "regular_price": "99.00",
    "sale_price": "69",
}


def one(row, sold_out=frozenset(), artists=()):
    return parse_event(row, SETTINGS, TZ, set(sold_out), list(artists))


def test_a_show_reads_in_full():
    event = one(ROW, artists=["משה אשכנזי"])[0]
    assert event.source_event_id == "87555"
    assert str(event.url) == "https://standupist.co.il/events/300127/"
    assert event.extra["purchase_url"] == "https://standupist.co.il/events/300127/"
    assert event.start_at == datetime(2027, 1, 31, 21, 15, tzinfo=ZoneInfo(TZ))
    assert event.end_at == datetime(2027, 1, 31, 22, 30, tzinfo=ZoneInfo(TZ))
    assert event.venue_name == "הבאBE בר"
    assert event.city == "הוד השרון"
    assert event.latitude == 32.13018511272441
    assert event.source_genres == ["סטנדאפ"]
    assert event.extra["show_types"] == ["הופעה מלאה"]
    assert event.artists == ["משה אשכנזי"]
    assert event.min_price == Decimal(69)
    assert event.tickets[0].original_price == Decimal(99)
    assert not event.is_sold_out


def test_weekly_repeats_are_one_event_per_date_keeping_the_wall_clock_over_the_clock_change():
    row = ROW | {
        "id": 84722,
        "permalink": "https://standupist.co.il/events/winter_hschena/var/ri-.l-L1",
        # 21:30 on 12 Oct (summer time) and on 9 Nov (winter time)
        "repeats": [[1791829800, 1791833400], [1794252600, 1794256200]],
        "regular_price": None,
        "sale_price": None,
        "event_type_3": {"1": "ללא עלות כספית"},
    }
    summer, winter = one(row, sold_out={"event_84722_1"})
    assert (summer.start_at.hour, summer.start_at.minute) == (21, 30)
    assert (winter.start_at.hour, winter.start_at.minute) == (21, 30)
    assert summer.source_event_id == "84722-20261012"
    assert str(winter.url).endswith("/events/winter_hschena/var/ri-1.l-L1/")
    assert summer.tickets[0].is_free
    assert not summer.is_sold_out
    assert winter.is_sold_out


def test_start_equal_to_end_means_no_real_time():
    event = one(ROW | {"start": 1794390420, "end": 1794390420})[0]
    assert event.time_known is False
    assert (event.start_at.hour, event.start_at.minute) == (0, 0)
    assert event.end_at is None


def test_draft_cancelled_and_test_events_are_skipped():
    assert one(ROW | {"post_status": "draft"}) == []
    assert one(ROW | {"event_status": "cancelled"}) == []
    assert one(ROW | {"name": "TEST"}) == []


def test_no_price_and_not_free_has_no_tiers():
    event = one(ROW | {"regular_price": None, "sale_price": ""})[0]
    assert event.tickets == []
    assert event.min_price is None


def test_escaped_quotes_and_missing_point():
    event = one(ROW | {"location_address": "קינג ג\\'ורג 48 תל אביב-יפו", "location_lat": "", "location_lon": ""})[0]
    assert event.address == "קינג ג'ורג 48 תל אביב-יפו"
    assert event.latitude is None
    assert event.longitude is None


def test_city_from_address_then_venue():
    assert find_city("טיילת הרמן מנקו 1, אור עקיבא", "מרכז המוסיקה") == "אור עקיבא"
    assert find_city("שמעון בר גיורא 29", "בר גיורא באר שבע") == "באר שבע"


def test_sold_out_cards():
    page = (
        '<div id="event_1_0" class="eventon_list_event"><span class="evo_soldout">אזל</span></div>'
        '<div id="event_2_0" class="eventon_list_event"></div>'
    )
    assert parse_sold_out(page) == {"event_1_0"}
    assert parse_sold_out("") == set()


def test_tag_slugs():
    assert tag_slugs(["post-1", "tag-moshe-ashkenazi", "event_type-full-show"]) == ["moshe-ashkenazi"]
    assert tag_slugs(None) == []
