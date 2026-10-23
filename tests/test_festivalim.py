"""Festivalim: the search listing and a festival page (trimmed real pages) to ScrapedEvent."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from partylinks.config import HttpSettings
from partylinks.scrapers import festivalim

FIXTURES = Path(__file__).parent / "fixtures" / "festivalim"
TZ = "Asia/Jerusalem"
SITE = "https://www.festivalim.co.il"
CFG = festivalim.FestivalimSettings(cron="43 4 * * *", site_url=SITE)


def _page(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_when_spans_whole_days() -> None:
    tz = ZoneInfo(TZ)
    start, end = festivalim.parse_when("יום רביעי, 30  בדצמבר, 2026 - יום שבת, 2  בינואר, 2027", TZ)
    assert (start, end) == (datetime(2026, 12, 30, tzinfo=tz), datetime(2027, 1, 3, tzinfo=tz))
    assert festivalim.parse_when("יום שישי, 19  במרץ, 2027", TZ) == (datetime(2027, 3, 19, tzinfo=tz), None)
    assert festivalim.parse_when("שנתי באוגוסט", TZ) == (None, None)


def test_listing_keeps_dated_festivals() -> None:
    events = festivalim.parse_listing(_page("listing.html"), TZ)
    assert [e.source_event_id for e in events] == ["15706-2026", "16923-2026"]  # undated entry skipped
    jazz = events[0]
    assert jazz.title == "פסטיבל ג'אז אילת בים האדום 2026" and jazz.city == "אילת" and jazz.country is None
    assert jazz.start_at == datetime(2026, 11, 11, tzinfo=ZoneInfo(TZ))
    assert jazz.end_at == datetime(2026, 11, 15, tzinfo=ZoneInfo(TZ))
    assert jazz.source_genres == ["ג'אז", "מוזיקה", "פסטיבלים"]
    assert jazz.image_url and "-300x200" not in jazz.image_url
    assert events[1].end_at is None  # one day


def test_details_add_description_image_and_site() -> None:
    listed = festivalim.parse_listing(_page("listing.html"), TZ)[0]
    event = festivalim.with_details(listed, _page("event.html"))
    assert event.extra["purchase_url"] == "http://redseajazz.co.il"
    assert event.image_url == f"{SITE}/wp-content/uploads/2018/12/Red_Sea_Jazz_Festival-Logo.jpg"
    assert event.description and event.description.startswith("מאז שנת 1987") and "adsbygoogle" not in event.description
    assert event.start_at == listed.start_at and event.source_event_id == listed.source_event_id


def test_scrape_stops_at_first_undated_page() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        if request.url.path == "/":
            return httpx.Response(200, text=_page("listing.html"))
        if request.url.path == "/page/2/":
            return httpx.Response(200, text="<html><article class='post-1'>לא נמצא מידע</article></html>")
        if "%d7%92%d7%90%d7%96" in str(request.url):  # the jazz festival's page
            return httpx.Response(200, text=_page("event.html"))
        return httpx.Response(500)

    scraper = festivalim.FestivalimScraper(
        CFG, HttpSettings(request_delay_seconds=0, backoff_seconds=0, max_retries=0), TZ
    )
    scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def run() -> list[festivalim.ScrapedEvent]:
        return [e async for e in scraper.scrape()]

    events = {e.source_event_id: e for e in asyncio.run(run())}
    assert set(events) == {"15706-2026", "16923-2026"}  # a failed festival page keeps the listed event
    assert events["15706-2026"].extra["purchase_url"] == "http://redseajazz.co.il"
    assert "/page/3/" not in requested
