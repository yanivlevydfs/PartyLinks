"""Askolot (the Open University's school of enrichment studies): the listing's courses, and only the groups one
can book alone (one meeting, or a ticket per meeting) as events. Fixtures saved 26.11.2026 and trimmed to the
listing cards and each course's header, text and groups: a one-off lecture-concert (aa7731ed), a one-off show
(d4787fc0), a series sold whole in Raanana and online (d628f1cc, "ראשים מדברים") and a tour series (00f83605)."""

from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.models import ScrapedEvent
from partylinks.scrapers import build_scraper
from partylinks.scrapers.ascolot import AscolotScraper, AscolotSettings, parse_course, parse_listing

FIXTURES = Path(__file__).parent / "fixtures" / "ascolot"
TZ = "Asia/Jerusalem"
ONE_OFF = "aa7731ed-620e-4f26-9346-686b16b05928"


def _settings() -> AscolotSettings:
    scraper = build_scraper("ascolot", Settings())
    assert isinstance(scraper, AscolotScraper)
    return scraper.settings


def _courses() -> dict[str, dict]:
    return {c["id"]: c for c in parse_listing((FIXTURES / "listing.html").read_text("utf-8"), _settings())}


def _events(prefix: str) -> list[ScrapedEvent]:
    course = next(c for i, c in _courses().items() if i.startswith(prefix))
    page = (FIXTURES / f"course_{prefix}.html").read_text("utf-8")
    return parse_course(page, course, _settings(), TZ)


def test_listing_gives_each_course_once_with_its_card_picture() -> None:
    courses = _courses()
    assert len(courses) == 4
    one_off = courses[ONE_OFF]
    assert one_off["url"].endswith(f"/allcourses/Course/{ONE_OFF}.aspx")
    assert one_off["image"] == "https://extra-academic.openu.ac.il/sites/ascolot/Lists/MediaServer_Images/gil.jpg"


def test_one_off_lecture_concert() -> None:
    [event] = _events("aa7731ed")
    assert event.title == "גיל שוחט מארח את הצ'לנית כריסטינה קופר"
    assert event.source_event_id == "83593-2026-11-30"
    assert event.start_at == datetime(2026, 11, 30, 17, 30, tzinfo=ZoneInfo(TZ))
    assert event.end_at == datetime(2026, 11, 30, 19, 0, tzinfo=ZoneInfo(TZ))
    assert (event.venue_name, event.city) == ("מוזיאון תל אביב לאמנות", "תל אביב")
    assert (event.latitude, event.longitude) == (32.0775956, 34.7866422)
    assert event.source_genres == ["מוסיקה ופזמון עברי"] and event.artists == ["גיל שוחט"]
    assert [t.price for t in event.tickets] == [Decimal(130)] and event.min_price == Decimal(130)
    assert event.extra["purchase_url"] == "https://extra-academic.openu.ac.il/registration/Group?school=C&group=83593"
    assert event.images and event.images[0].endswith("/gil.jpg")
    assert event.description and "ברליוז" in event.description
    enriched = Enricher(Settings().enrichment, TZ).enrich(event)
    assert enriched is not None and enriched.genres


def test_one_off_show_next_year_in_a_raanana_hall() -> None:
    [event] = _events("d4787fc0")
    assert event.start_at == datetime(2027, 3, 9, 19, 0, tzinfo=ZoneInfo(TZ))
    assert event.end_at == datetime(2027, 3, 9, 20, 30, tzinfo=ZoneInfo(TZ))
    assert (event.venue_name, event.city) == ("האוניברסיטה הפתוחה - קמפוס רעננה", "רעננה")
    assert event.latitude is not None and event.min_price == Decimal(120)


def test_series_sold_whole_online_groups_and_tours_are_no_events() -> None:
    assert _events("d628f1cc") == []  # 8 meetings in Raanana (865 ₪ for all) and an online group
    assert _events("00f83605") == []  # a tour series


def test_run_reads_the_listing_and_each_course_once() -> None:
    scraper = build_scraper("ascolot", Settings())
    assert isinstance(scraper, AscolotScraper) and scraper.settings.skip_title_words
    scraper.http.request_delay_seconds = 0
    asked: list[str] = []
    pages = {i: (FIXTURES / f"course_{i[:8]}.html").read_text("utf-8") for i in _courses()}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        asked.append(url)
        if "/coursefilter/" in url:
            assert request.url.params["pageSize"] == str(scraper.settings.page_size)
            return httpx.Response(200, text=(FIXTURES / "listing.html").read_text("utf-8"))
        course_id = url.rsplit("/", 1)[-1].removesuffix(".aspx")
        return httpx.Response(200, text=pages[course_id])

    async def go() -> list[ScrapedEvent]:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return [e async for e in scraper.events()]

    events = asyncio.run(go())
    assert len(asked) == 1 + len(pages) and not scraper.failures
    starts = [e.start_at.timestamp() for e in events if e.start_at]
    assert starts == sorted(starts)  # nearest first; past ones (once the fixture's days pass) are left out
    one_offs = {e.source_event_id for e in _events("aa7731ed") + _events("d4787fc0")}
    assert {e.source_event_id for e in events} <= one_offs  # the series and the tour: none
