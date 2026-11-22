"""Havana Music Club: the Wix ticket page's repeater (styles, weekday, date, event page) -> nights."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.models import ScrapedEvent
from partylinks.scrapers import build_scraper
from partylinks.scrapers.havanaclub import HavanaClubScraper, HavanaClubSettings, parse_listing, parse_night

TZ = "Asia/Jerusalem"
# The ticket page's six repeater items as served on 22.11.2026 (the rest of the Wix page left out).
PAGE = (Path(__file__).parent / "fixtures/havanaclub/tickets.html").read_text("utf-8")


def _settings() -> HavanaClubSettings:
    s = build_scraper("havanaclub", Settings()).settings
    assert isinstance(s, HavanaClubSettings)
    return s


def test_listing_items() -> None:
    nights = parse_listing(PAGE)
    assert [(n["weekday"], n["date"]) for n in nights] == [
        ("יום שלישי", "24.11.2026"), ("יום חמישי", "26.11.2026"), ("יום שבת", "28.11.2026"),
        ("יום שלישי", "01.12.2026"), ("יום חמישי", "03.12.2026"), ("יום שבת", "05.12.2026"),
    ]  # fmt: skip
    assert nights[0] == {
        "id": "0085168e-138a-43ea-b94f-d96f3643526d",
        "styles": "סלסה | בצ׳אטה",
        "weekday": "יום שלישי",
        "date": "24.11.2026",
    }
    assert nights[1]["styles"] == "סלסה | בצ׳אטה | אורבן קיז"


def test_night_event_from_config_schedule() -> None:
    s = _settings()
    tuesday, thursday = (parse_night(n, s, TZ) for n in parse_listing(PAGE)[:2])
    assert tuesday is not None and thursday is not None
    assert tuesday.start_at.isoformat() == "2026-11-24T21:30:00+02:00"  # Tuesday's first lesson
    assert thursday.start_at.isoformat() == "2026-11-26T21:00:00+02:00"  # doors
    assert tuesday.end_at is None and tuesday.time_known
    assert str(thursday.url) == "https://www.havanaclub.co.il/event/181da723-165a-4c44-95e3-69cf6d3efa9e"
    assert thursday.title == "מסיבת סלסה | בצ׳אטה | אורבן קיז בהוואנה קלאב"
    assert thursday.source_genres == ["סלסה", "בצ׳אטה", "אורבן קיז"]
    assert (thursday.venue_name, thursday.city, thursday.latitude) == ("הוואנה מיוזיק קלאב", "תל אביב-יפו", 32.0743385)
    enriched = Enricher(Settings().enrichment, TZ).enrich(thursday)
    assert enriched is not None and {"latin_dance", "salsa"} <= set(enriched.genres)
    assert thursday.images == []  # no banner given
    banner = "https://static.wixstatic.com/media/076108_a816021c368349598deecce74044a957~mv2.png"
    assert (parse_night(parse_listing(PAGE)[0], s, TZ, banner) or thursday).images == [banner]


def test_items_without_a_date_link_or_style_are_skipped() -> None:
    s = _settings()
    night = parse_listing(PAGE)[0]
    assert parse_night({**night, "date": ""}, s, TZ) is None
    assert parse_night({**night, "id": ""}, s, TZ) is None
    assert parse_night({**night, "styles": ""}, s, TZ) is None
    # A weekday with no configured start: the default (doors at 21:00).
    friday = parse_night({**night, "weekday": "יום שישי"}, s, TZ)
    assert friday is not None and (friday.start_at.hour, friday.start_at.minute) == (21, 0)


def test_run_reads_one_page_and_yields_upcoming_nights_soonest_first() -> None:
    scraper = build_scraper("havanaclub", Settings())
    assert isinstance(scraper, HavanaClubScraper)
    scraper.http.request_delay_seconds = 0
    asked: list[str] = []
    banner = "https://static.wixstatic.com/media/076108_a816021c368349598deecce74044a957~mv2.png"
    page = f'<head><meta property="og:image" content="{banner}"></head>' + PAGE

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        return httpx.Response(200, text=page, headers={"content-type": "text/html; charset=utf-8"})

    async def go() -> list[ScrapedEvent]:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return [e async for e in scraper.scrape()]

    events = asyncio.run(go())
    assert len(asked) == 1  # one request a run
    starts = [e.start_at.timestamp() for e in events if e.start_at]
    assert starts == sorted(starts)  # nearest first; past nights (once the fixture's dates pass) are left out
    assert len(events) <= 6 and not scraper.failures
    assert all(e.images == [banner] for e in events)  # the club's banner, the page's og:image
