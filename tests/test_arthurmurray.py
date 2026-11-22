"""Arthur Murray Israel: The Salon's Wix page -> its page JSON -> the embedded landing -> the evening."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.models import ScrapedEvent
from partylinks.parsing import og_image
from partylinks.scrapers import build_scraper
from partylinks.scrapers.arthurmurray import (
    ArthurMurrayScraper,
    ArthurMurraySettings,
    landing_urls,
    page_json_name,
    parse_landing,
    wix_original,
)

TZ = "Asia/Jerusalem"
FIXTURES = Path(__file__).parent / "fixtures/arthurmurray"
# As served on 23.11.2026: the Wix page's og:image and router config (the rest of the page left out), the
# page JSON's iframe, and the landing (its styles, scripts and inline base64 pictures left out).
PAGE = (FIXTURES / "salon_page.html").read_text("utf-8")
LAYOUT = json.loads((FIXTURES / "page.json").read_text("utf-8"))
LANDING = (FIXTURES / "landing.html").read_text("utf-8")
PAGE_URL = "https://www.arthurmurray.co.il/the-salon"
LANDING_URL = "https://instant-ghgxhwlrqezc-sasha99886-140a.wix-site-host.com/"
POSTER = "https://static.wixstatic.com/media/bd82e8_9aed223c5de14f06be5d8a5b00cc9fc0~mv2.jpg"
PHOTO = "https://static.wixstatic.com/media/c6ddcc_fb7a6afc52a04f20b6d855e24d60353f~mv2.jpg"
JSON_URL = "https://static.wixstatic.com/sites/f888f7_0e517fcff82fa98a5fc64e5c5b4dafa9_440.json.z?v=3"


def _settings(**http: object) -> ArthurMurraySettings:
    """The config block's values (config/settings.yaml ``sources.arthurmurray``)."""
    return ArthurMurraySettings(
        cron="0 0 * * *",
        venue_name="ארתור מארי רעננה",
        address="אחוזה 128, רעננה",
        city="רעננה",
        latitude=32.1810665,
        longitude=34.8732052,
        http=dict(http),
    )


def test_page_json_iframe_and_photo() -> None:
    assert page_json_name(PAGE, "the-salon") == "f888f7_0e517fcff82fa98a5fc64e5c5b4dafa9_440"
    assert page_json_name(PAGE, "events") == "f888f7_8dcc24a81af690c699ba45df17d0c190_440"
    assert page_json_name(PAGE, "haifa") is None
    assert landing_urls(LAYOUT) == [LANDING_URL]
    assert landing_urls({"data": {"document_data": {"x": {"type": "Image", "url": "https://x.example/"}}}}) == []
    # The page's own photo, without Wix's resize transform.
    assert wix_original(og_image(PAGE)) == PHOTO
    assert wix_original("data:image/jpeg;base64,") is None


def test_landing_evening() -> None:
    event = parse_landing(LANDING, _settings(), TZ, PAGE_URL, [PHOTO])
    assert event is not None
    assert event.title == "The Salon: Fred & Ginger עם רון פוגל"
    assert event.source_event_id == "salon-2026-12-05"
    assert event.start_at is not None and event.start_at.isoformat() == "2026-12-05T20:00:00+02:00"  # not the doors
    assert event.time_known and event.end_at is None
    assert event.extra == {
        "purchase_url": "https://live.payme.io/sale/template/SALE1792-841232WO-6ZPINRX9-WXRXVXRB",
        "doors_open": "19:30",
    }
    assert str(event.url) == PAGE_URL
    assert (event.venue_name, event.address, event.city) == ("ארתור מארי רעננה", "אחוזה 128, רעננה", "רעננה")
    assert (event.latitude, event.longitude) == (32.1810665, 34.8732052)
    # Single and couple; the students' price needs the studio's code.
    assert [(t.name, t.price, t.admits) for t in event.tickets] == [("כרטיס יחיד", 350, 1), ("כרטיס זוגי", 680, 2)]
    assert (event.min_price, event.max_price) == (350, 680)
    assert event.source_genres == ["ריקודים סלוניים", "הרצאה וקטעי קולנוע", "שמפניה וכיבוד קל", "ריקוד באווירה נעימה"]
    assert event.images == [POSTER, PHOTO]  # the guest's video poster, then the page's photo
    assert event.description and event.description.startswith("The Salon by Arthur Murray")
    enriched = Enricher(Settings().enrichment, TZ).enrich(event)
    assert enriched is not None and {"ballroom", "lectures"} <= set(enriched.genres)


def test_landing_fallbacks() -> None:
    s = _settings()
    # No hero: the name from the page title, the date from it too, the start from the text.
    bare = (
        LANDING.replace('class="hero"', 'class="top"')
        .replace('class="sub"', 'class="x"')
        .replace('class="date"', 'class="y"')
    )
    event = parse_landing(bare, s, TZ, PAGE_URL)
    assert event is not None and event.title == "The Salon | Fred & Ginger"
    assert event.start_at is not None and event.start_at.isoformat() == "2026-12-05T20:00:00+02:00"
    # Only the doors: they are the start.
    doors = parse_landing(LANDING.replace("תחילת האירוע", "סיום"), s, TZ, PAGE_URL)
    assert doors is not None and doors.start_at is not None
    assert (doors.start_at.hour, doors.start_at.minute) == (19, 30)
    # No time at all: the day, time unknown.
    undated = LANDING.replace("תחילת האירוע", "סיום").replace("פתיחת דלתות", "קבלת פנים")
    day = parse_landing(undated, s, TZ, PAGE_URL)
    assert day is not None and day.start_at is not None
    assert not day.time_known and day.start_at.hour == 0
    # No date anywhere it is looked for: nothing.
    no_date = LANDING.replace("5.12.2026", "בקרוב")
    assert parse_landing(no_date, s, TZ, PAGE_URL) is None


def _run(scraper: ArthurMurrayScraper, routes: dict[str, httpx.Response]) -> tuple[list[ScrapedEvent], list[str]]:
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return routes.get(str(request.url), httpx.Response(404))

    async def go() -> list[ScrapedEvent]:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return [e async for e in scraper.scrape()]

    return asyncio.run(go()), asked


def test_run_reads_three_pages() -> None:
    scraper = ArthurMurrayScraper(_settings(request_delay_seconds=0), Settings().http, TZ)
    html = {"content-type": "text/html; charset=utf-8"}
    routes = {
        PAGE_URL: httpx.Response(200, text=PAGE, headers=html),
        JSON_URL: httpx.Response(200, json=LAYOUT),
        LANDING_URL: httpx.Response(200, text=LANDING, headers=html),
    }
    events, asked = _run(scraper, routes)
    assert asked == list(routes)  # the page, its JSON, the landing
    assert not scraper.failures
    assert len(events) <= 1  # once 5.12.2026 has passed, nothing
    for event in events:
        assert event.title == "The Salon: Fred & Ginger עם רון פוגל" and event.images == [POSTER, PHOTO]


def test_no_salon_page_or_iframe_is_no_events() -> None:
    scraper = ArthurMurrayScraper(_settings(request_delay_seconds=0, max_retries=0), Settings().http, TZ)
    events, _ = _run(scraper, {})  # the page removed (404)
    assert events == [] and not scraper.failures
    html = {"content-type": "text/html; charset=utf-8"}
    routes = {
        PAGE_URL: httpx.Response(200, text=PAGE, headers=html),
        JSON_URL: httpx.Response(200, json={"data": {}}),
    }
    events, asked = _run(scraper, routes)
    assert events == [] and len(asked) == 2 and not scraper.failures


def test_config_block() -> None:
    s = build_scraper("arthurmurray", Settings()).settings
    assert isinstance(s, ArthurMurraySettings)
    assert (s.venue_name, s.city, s.latitude, s.longitude) == ("ארתור מארי רעננה", "רעננה", 32.1810665, 34.8732052)
    assert s.skip_title_words
