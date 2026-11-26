"""HaMesila (hamesila.org.il): the wp-json ``events`` list + each event page's Elementor template -> events.

Fixtures (22.11.2026): the list and taxonomy as served (only the fields read), and five event pages cut to
the single-post template (scripts, spacers and the sticky bar's code left out): a Smarticket lecture with a
day and no year, two lectures sold elsewhere (a full date; two prices), the exhibition (opening hours, no
day) and a hub page that lists other events. Courses and workshops (26.11.2026): their list and taxonomy, and
seven pages cut the same way: a one-off workshop, a series sold per meeting, a run of days sold per day, a
weekly class, a course sold whole with a free first meeting, a course of dated meetings sold whole, a weekly
class "from 6.9".
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import unquote
from zoneinfo import ZoneInfo

import httpx

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.models import ScrapedEvent
from partylinks.scrapers import build_scraper
from partylinks.scrapers.hamesila import HaMesilaScraper, HaMesilaSettings, parse_event, parse_prices, parse_workshop

TZ = "Asia/Jerusalem"
FIXTURES = Path(__file__).parent / "fixtures" / "hamesila"
POSTS = {p["id"]: p for p in json.loads((FIXTURES / "events.json").read_text("utf-8"))}
TAXONOMY = {t["id"]: t["name"] for t in json.loads((FIXTURES / "event_taxonomy.json").read_text("utf-8"))}
WORKSHOPS = {p["id"]: p for p in json.loads((FIXTURES / "workshops.json").read_text("utf-8"))}
COURSE_TAXONOMY = {t["id"]: t["name"] for t in json.loads((FIXTURES / "course_taxonomy.json").read_text("utf-8"))}
# As in the config block (the pin: OSM's "המסילה", amenity=community_centre).
CFG = HaMesilaSettings(cron="0 0 * * *", latitude=32.1702435, longitude=34.8189553)


def _page(post_id: int) -> str:
    return (FIXTURES / f"event_{post_id}.html").read_text("utf-8")


def _event(post_id: int) -> ScrapedEvent | None:
    return parse_event(POSTS[post_id], _page(post_id), TAXONOMY, CFG, TZ)


def test_smarticket_lecture_day_without_year() -> None:
    event = _event(5257)
    assert event is not None
    assert (event.source, event.source_event_id) == ("hamesila", "5257")
    assert event.title == "צמצום בזבוז מזון | ד\"ר אורי מאיר צ'יזיק"
    # "יום חמישי | 26/11 20:00 פתיחת דלתות: 19:30": the year the post was published (22.10.2026), not the doors.
    assert event.start_at == datetime(2026, 11, 26, 20, 0, tzinfo=ZoneInfo(TZ))
    assert event.time_known and event.end_at is None
    assert str(event.url).startswith("https://hamesila.org.il/events/")
    assert (event.venue_name, event.address, event.city) == (
        "המסילה ב׳ - תרבות, פנאי וקהילה",
        "הזמר העברי 5, הרצליה",
        "הרצליה",
    )
    assert (event.latitude, event.longitude) == (32.1702435, 34.8189553)
    assert event.source_genres == ["חודש הקיימות"]
    # The Smarticket box office's page, without the utm parameters: dedup merges it with smarticket's listing.
    assert event.extra == {"purchase_url": "https://hamesila-bet.smarticket.co.il/event/66"}
    assert [(t.name, t.price) for t in event.tickets] == [("כרטיס", Decimal(55))]
    assert (event.min_price, event.max_price) == (Decimal(55), Decimal(55))
    assert event.images == ["https://hamesila.org.il/wp-content/uploads/2026/10/upld6ad9eb40605da301849360.jpg"]
    assert event.description and "בזבוז המזון" in event.description
    enriched = Enricher(Settings().enrichment, TZ).enrich(event)
    assert enriched is not None and enriched.genres


def test_lectures_sold_elsewhere_full_date_and_two_prices() -> None:
    japan = _event(5603)
    assert japan is not None
    assert japan.start_at == datetime(2027, 2, 2, 20, 0, tzinfo=ZoneInfo(TZ))  # "יום שלישי | 2.2.2027 | 20:00"
    assert japan.extra["purchase_url"] == "https://tarbuta.com/product/tamar-7/"
    assert japan.source_genres == [] and [t.price for t in japan.tickets] == [Decimal(98)]
    brain = _event(5640)
    assert brain is not None
    assert brain.start_at == datetime(2027, 2, 23, 20, 30, tzinfo=ZoneInfo(TZ))
    assert [(t.name, t.price, t.admits) for t in brain.tickets] == [
        ("כרטיס יחיד", Decimal(80), 1),
        ("כרטיס זוגי", Decimal(150), 2),
    ]
    assert (brain.min_price, brain.max_price) == (Decimal(80), Decimal(150))
    assert brain.extra["purchase_url"] == "https://liatyakir.com/academy/brain-lecture/"
    enriched = Enricher(Settings().enrichment, TZ).enrich(brain)
    assert enriched is not None and "lectures" in enriched.genres


def test_pages_without_a_day_are_skipped() -> None:
    assert _event(5395) is None  # the exhibition: "א'- ה' 09:00 – 19:00 שישי 9:00-12:00"
    assert _event(5329) is None  # a hub page: its "26/11 20:00" is another event's day
    lecture = POSTS[5257]
    assert parse_event({**lecture, "date": "soon"}, _page(5257), TAXONOMY, CFG, TZ) is None
    assert parse_event({**lecture, "link": ""}, _page(5257), TAXONOMY, CFG, TZ) is None


def test_a_theme_hub_listed_as_an_event_is_no_event() -> None:
    # "דצמבר של אור" is an `events` post linked to /נושא/<theme>/: its first day is the Iran lecture's.
    hub = {**POSTS[5257], "link": "https://hamesila.org.il/%d7%a0%d7%95%d7%a9%d7%90/%d7%93%d7%a6%d7%9e%d7%91%d7%a8/"}
    assert parse_event(hub, _page(5257), TAXONOMY, CFG, TZ) is None


def _sessions(post_id: int) -> list[ScrapedEvent]:
    page = (FIXTURES / f"workshop_{post_id}.html").read_text("utf-8")
    return parse_workshop(WORKSHOPS[post_id], page, COURSE_TAXONOMY, CFG, TZ)


def _at(day: str, clock: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=ZoneInfo(TZ))


def test_one_off_workshop() -> None:
    # "יום שני 7/12/26 10:00-11:30", "עלות: 60 ₪", the Arbox sign-up button.
    (event,) = _sessions(5537)
    assert event.title == "תודעת תפוז | סדנא לילדים עם יולי מרוז" and event.source_event_id == "5537-2026-12-07"
    assert (event.start_at, event.end_at, event.time_known) == (
        _at("2026-12-07", "10:00"),
        _at("2026-12-07", "11:30"),
        True,
    )
    assert (event.min_price, event.max_price) == (Decimal(60), Decimal(60))
    assert "arboxapp.com" in str(event.extra["purchase_url"])
    assert event.source_genres[0] == "workshop" and "קורסים וסדנאות" in event.source_genres
    assert event.latitude and event.city == "הרצליה" and event.description and event.images


def test_series_sold_per_meeting_runs_into_next_year() -> None:
    # "11:00-14:00 10.11 8.12 5.1 9.2 9.3", "1250 ש"ח עבור הסדרה 300 ש"ח עבור מפגש בודד" (edited 4.11.2026).
    events = _sessions(5402)
    assert [e.start_at.date().isoformat() for e in events if e.start_at] == [
        "2026-11-10",
        "2026-12-08",
        "2027-01-05",
        "2027-02-09",
        "2027-03-09",
    ]
    assert {e.min_price for e in events} == {Decimal(300)} and events[0].end_at == _at("2026-11-10", "14:00")
    assert len({e.source_event_id for e in events}) == 5


def test_run_of_days_sold_per_day() -> None:
    # "6-10/12 | 09:30-13:30", "1,350 ₪ לכל הסדנה | 300 ₪ ליום בודד"; no sign-up button.
    events = _sessions(5593)
    assert [e.start_at for e in events] == [_at(f"2026-12-{d:02d}", "09:30") for d in range(6, 11)]
    assert {e.min_price for e in events} == {Decimal(300)} and all("purchase_url" not in e.extra for e in events)


def test_classes_and_courses_sold_whole_are_no_events() -> None:
    assert _sessions(1548) == []  # weekly class, "95 ₪ לשיעור בודד": no days
    assert _sessions(4659) == []  # "25/11 מפגש חשיפה ... 2/12 9/12 ...", "590 ₪ עבור חמישה מפגשים"
    assert _sessions(4252) == []  # five dated meetings, "750 ₪" for the course
    assert _sessions(2638) == []  # "ימי ראשון 9:00-10:00 החל מה-6.9", a monthly fee


def test_day_published_late_in_the_year_is_next_year() -> None:
    # A "2/1" day on a page published in December is January of the next year.
    page = _page(5257).replace("26/11", "2/1")
    event = parse_event({**POSTS[5257], "date": "2026-12-20T10:00:00"}, page, TAXONOMY, CFG, TZ)
    assert event is not None and event.start_at is not None and event.start_at.date().isoformat() == "2027-01-02"


def test_prices() -> None:
    assert [(t.price, t.is_free) for t in parse_prices("ללא תשלום", CFG)] == [(Decimal(0), True)]
    assert [(t.name, t.price) for t in parse_prices("עלות כרטיס: 98 ₪", CFG)] == [("כרטיס", Decimal(98))]
    assert parse_prices("האירוע יתקיים במינימום נרשמים", CFG) == []


def test_run_reads_the_list_the_taxonomy_and_each_page() -> None:
    scraper = build_scraper("hamesila", Settings())
    assert isinstance(scraper, HaMesilaScraper)
    assert scraper.settings.latitude is not None and scraper.settings.skip_title_words
    scraper.http.request_delay_seconds = 0
    asked: list[str] = []
    pages = {unquote(str(POSTS[i]["link"])): _page(i) for i in POSTS if (FIXTURES / f"event_{i}.html").exists()}
    pages |= {
        unquote(str(p["link"])): (FIXTURES / f"workshop_{i}.html").read_text("utf-8") for i, p in WORKSHOPS.items()
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        asked.append(url)
        if "/wp-json/wp/v2/events" in url:
            return httpx.Response(200, json=list(POSTS.values()))
        if "/wp-json/wp/v2/event_taxonomy" in url:
            return httpx.Response(200, json=[{"id": k, "name": v} for k, v in TAXONOMY.items()])
        if "/wp-json/wp/v2/course_workshop" in url:
            return httpx.Response(200, json=list(WORKSHOPS.values()))
        if "/wp-json/wp/v2/taxonomy-course" in url:
            return httpx.Response(200, json=[{"id": k, "name": v} for k, v in COURSE_TAXONOMY.items()])
        # The posts with no saved page get a page with no template: no event, no failure.
        return httpx.Response(200, text=pages.get(unquote(url), "<html><body></body></html>"))

    async def go() -> list[ScrapedEvent]:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return [e async for e in scraper.events()]

    events = asyncio.run(go())
    assert len(asked) == 4 + len(POSTS) + len(WORKSHOPS)  # two lists + two taxonomies + one page per post
    starts = [e.start_at.timestamp() for e in events if e.start_at]
    assert starts == sorted(starts)  # nearest first; past ones (once the fixture's days pass) are left out
    sessions = {e.source_event_id.partition("-")[0] for e in events if "-" in e.source_event_id}
    assert {e.source_event_id for e in events if "-" not in e.source_event_id} <= {"5257", "5603", "5640"}
    assert sessions <= {"5537", "5402", "5593"} and not scraper.failures  # classes and whole courses: none
