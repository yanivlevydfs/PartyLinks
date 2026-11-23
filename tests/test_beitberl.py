from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from partylinks.scrapers.beitberl import BeitBerlSettings, Place, build_events, current, parse_page, resolve_place

FIXTURES = Path(__file__).parent / "fixtures" / "beitberl"
TZ = "Asia/Jerusalem"
SITE = "https://www.beitberl.ac.il"


def settings() -> BeitBerlSettings:
    return BeitBerlSettings(
        cron="0 0 * * *",
        city="כפר סבא",
        campus=Place(venue_name="המכללה האקדמית בית ברל", address="בית ברל", latitude=32.2014, longitude=34.9264),
        places={
            "בניין סים": Place(venue_name="בניין סים - בית ברל", address="בית ברל", latitude=32.2, longitude=34.93)
        },
        campus_words=["חרוב", "בניין", "חדר"],
        labels={"יום עיון": ["עיון", "הרצאה"], "סיגד": ["תרבות"]},
        private_words=["יום פתוח", "טקס"],
    )


def page(name: str):
    html = (FIXTURES / f"{name}.html").read_text(encoding="utf-8")
    parsed = parse_page(html, f"{SITE}/event/{name}/", settings())
    assert parsed is not None
    return parsed


def test_parse_page_reads_day_hours_place_text_and_artwork():
    sigd = page("sigd-3")
    assert sigd.post_id == "75904"
    assert sigd.title == "יום הסיגד"
    assert [d.isoformat() for d in sigd.days] == ["2026-11-08"]
    assert sigd.start is not None and sigd.end is not None
    assert (sigd.start.isoformat(), sigd.end.isoformat()) == ("12:00:00", "14:00:00")
    assert sigd.place == "חרוב 801/2"
    assert sigd.text and "סולידריות חברתית" in sigd.text
    assert sigd.image and sigd.image.endswith("/wp-content/uploads/2026/10/sigd-26-1845x1080.jpg")
    assert sigd.links == ["https://sites.google.com/view/sigdbeitberl/%D7%91%D7%99%D7%AA"]


def test_build_events_places_a_hall_and_maps_the_title():
    book = page("iyun-book")
    [event] = build_events(book, f"{SITE}/event/iyun-book/", settings(), TZ)
    assert event.source_event_id == book.post_id
    assert event.start_at == datetime(2026, 11, 18, 12, 0, tzinfo=ZoneInfo(TZ))
    assert event.end_at == datetime(2026, 11, 18, 16, 30, tzinfo=ZoneInfo(TZ))
    assert event.time_known
    assert (event.venue_name, event.city, event.latitude) == ("בניין סים - בית ברל", "כפר סבא", 32.2)
    assert event.extra["room"] == "בניין סים (בניין 500), חדר 109"
    assert event.source_genres == ["יום עיון", "עיון", "הרצאה"]
    assert event.images and event.image_url == event.images[0]
    assert "purchase_url" not in event.extra  # the site's own PDF is no ticket link


def test_a_campus_room_takes_the_campus_and_a_link_elsewhere_is_kept():
    sigd = page("sigd-3")
    [event] = build_events(sigd, f"{SITE}/event/sigd-3/", settings(), TZ)
    assert (event.venue_name, event.latitude, event.longitude) == ("המכללה האקדמית בית ברל", 32.2014, 34.9264)
    assert event.extra["purchase_url"].startswith("https://sites.google.com/")


def test_a_past_day_is_not_current():
    sigd = page("sigd-3")
    assert current(build_events(sigd, f"{SITE}/event/sigd-3/", settings(), TZ), TZ) == []


def test_place_lines():
    s = settings()
    assert resolve_place(None, s) is s.campus
    assert resolve_place("חדר 12", s) is s.campus
    assert resolve_place("מוזיאון תל אביב לאמנות", s) is None  # elsewhere: the line is the venue, geocoded


def test_online_and_private_events_are_not_imported():
    s = settings()
    sigd = page("sigd-3")
    for title, place in (("יום פתוח לתואר ראשון", None), ("טקס חלוקת מלגות", None), ("הרצאה", "בזום")):
        sigd.title, sigd.place = title, place
        assert build_events(sigd, f"{SITE}/event/sigd-3/", s, TZ) == []
