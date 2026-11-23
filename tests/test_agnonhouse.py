"""Agnon House (agnonhouse.org.il): the coming-events listing, then each event's page.

Fixtures (23.11.2026), cut to the page's ``<main>`` (forms and scripts removed): the listing of five coming
events, a lecture at the house (Levinas, 27.11), an online lecture ("הרצאה מקוונת") and an English Zoom talk.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.models import ScrapedEvent
from partylinks.scrapers import build_scraper
from partylinks.scrapers.agnonhouse import AgnonHouseScraper, AgnonHouseSettings, listing_pages, parse_page

TZ = "Asia/Jerusalem"
IL = ZoneInfo(TZ)
FIXTURES = Path(__file__).parent / "fixtures" / "agnonhouse"
LEVINAS = "https://agnonhouse.org.il/event/%d7%9e%d7%9f-%d7%94%d7%a7%d7%99%d7%95%d7%9d-%d7%90%d7%9c-%d7%94%d7%a7%d7%99%d7%99%d7%9d-4-2/"
ONLINE = "https://agnonhouse.org.il/event/%d7%97%d7%99%d7%99%d7%98%d7%99%d7%9d-%d7%91%d7%a8%d7%95%d7%97-3-2/"
ZOOM_EN = "https://agnonhouse.org.il/event/dara-horn/"
CFG = AgnonHouseSettings.model_validate(
    {
        "cron": "0 0 * * *",
        "venue_name": "בית עגנון",
        "address": "קלאוזנר 16, תלפיות",
        "city": "ירושלים",
        "latitude": 31.7486205,
        "longitude": 35.2238491,
    }
)


def _html(name: str) -> str:
    return (FIXTURES / f"{name}.html").read_text("utf-8")


def test_listing_gives_the_pages_soonest_first() -> None:
    pages = listing_pages(_html("events"))
    assert len(pages) == 5
    assert pages[1] == LEVINAS and pages[-1] == ZOOM_EN


def test_lecture_at_the_house() -> None:
    event = parse_page(_html("levinas"), LEVINAS, CFG, TZ)
    assert event is not None
    assert (event.source, event.source_event_id) == ("agnonhouse", "110505")  # the body's postid
    assert event.title == "לוינס בשיחה עם קירקגור"
    assert event.start_at == datetime(2026, 11, 27, 10, 0, tzinfo=IL) and event.time_known
    assert (event.venue_name, event.address, event.city) == ("בית עגנון", "קלאוזנר 16, תלפיות", "ירושלים")
    assert (event.latitude, event.longitude) == (31.7486205, 35.2238491)
    assert event.description and event.description.startswith("הרצאה בבית עגנון עם הרב דניאל אפשטיין")
    assert event.images and event.images[0].startswith("https://agnonhouse.org.il/wp-content/uploads/")
    assert event.extra["purchase_url"] == LEVINAS  # registration is the page's own form
    enriched = Enricher(Settings().enrichment, TZ).enrich(event)
    assert enriched is not None and "lectures" in enriched.genres


def test_online_lectures_are_not_imported() -> None:
    assert parse_page(_html("online"), ONLINE, CFG, TZ) is None  # "הרצאה מקוונת"
    assert parse_page(_html("zoom_en"), ZOOM_EN, CFG, TZ) is None  # "A Zoom event from ..."


def test_a_place_without_the_house_keeps_its_name_and_the_city() -> None:
    html = _html("levinas").replace("בית עגנון בירושלים</span>", "הספרייה הלאומית</span>")
    event = parse_page(html, LEVINAS, CFG, TZ)
    assert event is not None
    assert (event.venue_name, event.address, event.city) == ("הספרייה הלאומית", None, "ירושלים")
    assert event.latitude is None


def test_no_date_no_event() -> None:
    assert parse_page(_html("levinas").replace("27.11.2026", ""), LEVINAS, CFG, TZ) is None


def test_run_reads_the_listing_and_each_page() -> None:
    scraper = build_scraper("agnonhouse", Settings())
    assert isinstance(scraper, AgnonHouseScraper)
    scraper.http.request_delay_seconds = 0
    pages = {LEVINAS: "levinas", ONLINE: "online", ZOOM_EN: "zoom_en"}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.url.path == "/events/":
            return httpx.Response(200, text=_html("events"))
        if url in pages:
            return httpx.Response(200, text=_html(pages[url]))
        return httpx.Response(200, text="<html><body></body></html>")  # the others: no date, skipped

    async def go() -> list[ScrapedEvent]:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return [e async for e in scraper.events()]

    events = asyncio.run(go())
    assert not scraper.failures
    assert [e.source_event_id for e in events if e.start_at and e.start_at > datetime(2026, 11, 26, tzinfo=IL)] in (
        ["110505"],
        [],  # once 27.11.2026 is past
    )
