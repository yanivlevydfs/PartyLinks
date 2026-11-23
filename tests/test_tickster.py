"""Tickster (tickster.co.il): the sitemap's event pages -> each page's prerendered JSON-LD Event.

Fixtures (23.11.2026): eight sitemap entries (the app's own pages and lists beside three event pages), a Kupat
Bravo show's page (Depeche Mode Symphony, Tel Aviv's music centre) and a Funzing workshop's (a street in Hod
HaSharon for its place).
"""

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
from partylinks.scrapers.tickster import TicksterScraper, TicksterSettings, event_urls, parse_event_page

TZ = "Asia/Jerusalem"
FIXTURES = Path(__file__).parent / "fixtures" / "tickster"
SITEMAP = (FIXTURES / "sitemap.xml").read_text("utf-8")
BRAVO = (FIXTURES / "depeche-mode.html").read_text("utf-8")
FUNZING = (FIXTURES / "funzing.html").read_text("utf-8")
CFG = TicksterSettings.model_validate({"cron": "0 0 * * *"})


def test_sitemap_event_pages_only() -> None:
    urls = event_urls(SITEMAP, CFG)
    assert len(urls) == 3  # not the home page, /events, the category or the city lists
    assert all(u.startswith("https://www.tickster.co.il/events/") and u.count("/") == 4 for u in urls)


def test_kupat_bravo_show() -> None:
    event = parse_event_page(BRAVO, "https://www.tickster.co.il/events/x", TZ)
    assert event is not None
    assert (event.source, event.source_event_id) == ("tickster", "214991")  # the seance id
    assert event.title == "Depeche Mode Symphony - מופע לאור מאות נרות"
    assert event.start_at == datetime(2027, 1, 28, 20, 0, tzinfo=ZoneInfo(TZ))  # 18:00 UTC
    assert (event.venue_name, event.city) == ("מרכז המוסיקה תל-אביב יפו", "תל אביב-יפו")
    assert event.address == "מרכז המוסיקה תל-אביב יפו, תל אביב-יפו"
    assert event.extra["purchase_url"] == "https://tickster.kartisim.co.il/announce/buy/214991"
    assert event.min_price == Decimal(136)
    assert event.images == ["https://katalog.co.il/show/gallery/202324.jpg"]
    assert event.description and "Depeche Mode" in event.description
    enriched = Enricher(Settings().enrichment, TZ).enrich(event)
    assert enriched is not None and enriched.genres


def test_funzing_workshop_on_a_street() -> None:
    event = parse_event_page(FUNZING, "https://www.tickster.co.il/events/y", TZ)
    assert event is not None
    assert event.venue_name is None  # "ששת הימים, הוד השרון" is a street, not a hall
    assert (event.address, event.city) == ("ששת הימים, הוד השרון", "הוד השרון")
    assert event.extra["purchase_url"].startswith("https://il.funzing.com/funz/")
    assert event.start_at == datetime(2026, 11, 27, 10, 0, tzinfo=ZoneInfo(TZ))
    assert parse_event_page("<html></html>", "https://www.tickster.co.il/events/z", TZ) is None


def test_run_reads_the_sitemap_then_each_page() -> None:
    scraper = build_scraper("tickster", Settings())
    assert isinstance(scraper, TicksterScraper)
    scraper.http.request_delay_seconds = 0
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path == "/sitemap.xml":
            return httpx.Response(200, text=SITEMAP)
        return httpx.Response(200, text=BRAVO if "Depeche" in request.url.path else FUNZING)

    async def go() -> list[ScrapedEvent]:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return [e async for e in scraper.events()]

    events = asyncio.run(go())
    assert asked[0] == "/sitemap.xml" and len(asked) == 4
    assert not any(p.startswith("/api/") for p in asked)  # robots.txt disallows the app's API
    assert not scraper.failures
    starts = [e.start_at.timestamp() for e in events if e.start_at]
    assert starts == sorted(starts)
