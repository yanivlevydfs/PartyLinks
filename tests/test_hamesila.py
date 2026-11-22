"""HaMesila (hamesila.org.il): the wp-json ``events`` list + each event page's Elementor template -> events.

Fixtures (22.11.2026): the list and taxonomy as served (only the fields read), and five event pages cut to
the single-post template (scripts, spacers and the sticky bar's code left out): a Smarticket lecture with a
day and no year, two lectures sold elsewhere (a full date; two prices), the exhibition (opening hours, no
day) and a hub page that lists other events.
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
from partylinks.scrapers.hamesila import HaMesilaScraper, HaMesilaSettings, parse_event, parse_prices

TZ = "Asia/Jerusalem"
FIXTURES = Path(__file__).parent / "fixtures" / "hamesila"
POSTS = {p["id"]: p for p in json.loads((FIXTURES / "events.json").read_text("utf-8"))}
TAXONOMY = {t["id"]: t["name"] for t in json.loads((FIXTURES / "event_taxonomy.json").read_text("utf-8"))}
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


def test_day_published_late_in_the_year_is_next_year() -> None:
    # A "2/1" day on a page published in December is January of the next year.
    page = _page(5257).replace("26/11", "2/1")
    event = parse_event({**POSTS[5257], "date": "2026-12-20T10:00:00"}, page, TAXONOMY, CFG, TZ)
    assert event is not None and event.start_at.date().isoformat() == "2027-01-02"


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

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        asked.append(url)
        if "/wp-json/wp/v2/events" in url:
            return httpx.Response(200, json=list(POSTS.values()))
        if "/wp-json/wp/v2/event_taxonomy" in url:
            return httpx.Response(200, json=[{"id": k, "name": v} for k, v in TAXONOMY.items()])
        # The posts with no saved page get a page with no template: no event, no failure.
        return httpx.Response(200, text=pages.get(unquote(url), "<html><body></body></html>"))

    async def go() -> list[ScrapedEvent]:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return [e async for e in scraper.events()]

    events = asyncio.run(go())
    assert len(asked) == 2 + len(POSTS)  # list + taxonomy + one page per post
    starts = [e.start_at.timestamp() for e in events if e.start_at]
    assert starts == sorted(starts)  # nearest first; past ones (once the fixture's days pass) are left out
    assert {e.source_event_id for e in events} <= {"5257", "5603", "5640"} and not scraper.failures
