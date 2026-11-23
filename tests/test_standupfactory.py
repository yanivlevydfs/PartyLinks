"""Stand Up Factory (standupfactory.co.il): the show board + each date's post (GoShow iframe) + wp-json categories.

Fixtures (23.11.2026): the board's first page (its 18 dates and the pagination, 6 pages), one date's post page
(only GoShow's sale iframe), wp-json's permalinks and categories for five of the dates, and the category names.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.models import ScrapedEvent
from partylinks.scrapers import build_scraper
from partylinks.scrapers.standupfactory import (
    StandupFactoryScraper,
    StandupFactorySettings,
    build_event,
    goshow_link,
    last_page,
    parse_board,
)

TZ = "Asia/Jerusalem"
IL = ZoneInfo(TZ)
FIXTURES = Path(__file__).parent / "fixtures" / "standupfactory"
BOARD = (FIXTURES / "board.html").read_text("utf-8")
POST = (FIXTURES / "post.html").read_text("utf-8")
POSTS = json.loads((FIXTURES / "posts.json").read_text("utf-8"))
CATEGORIES = json.loads((FIXTURES / "categories.json").read_text("utf-8"))
CLUB = (32.0506683, 34.7726901)
CFG = StandupFactorySettings.model_validate({
    "cron": "0 0 * * *",
    "latitude": CLUB[0],
    "longitude": CLUB[1],
    "series_categories": ["ערב גסויות", "סטנדאפ באנגלית"],
})  # fmt: skip


def test_board_dates_nearest_first() -> None:
    cards = parse_board(BOARD, CFG, TZ)
    assert len(cards) == 18
    assert last_page(BOARD) == 6
    first = cards[0]
    assert (first.post_id, first.title) == ("27188", "נועם אונגר במופע סטנדאפ")
    assert first.start_at == datetime(2026, 11, 23, 21, 30, tzinfo=IL)
    assert first.url == "https://www.standupfactory.co.il/?p=27188"
    # The cover first, then the square; Hebrew file names percent-encoded.
    assert first.images[0].startswith("https://www.standupfactory.co.il/wp-content/uploads/2026/04/%D7%A0")
    assert first.images[0].endswith(".gif") and len(first.images) == 2
    assert not first.sold_out
    starts = [c.start_at for c in cards]
    assert starts == sorted(starts)


def test_sold_out_artwork_marks_the_date() -> None:
    cards = {c.post_id: c for c in parse_board(BOARD, CFG, TZ)}
    assert cards["27314"].sold_out  # "טל-ראשון-קאבר-אזלו.gif"
    # The midnight show's title says 00:15; the board (and GoShow) date it 23:45.
    late = next(c for c in cards.values() if c.title.endswith("00:15"))
    assert late.start_at == datetime(2026, 11, 27, 23, 45, tzinfo=IL)


def test_post_page_gives_the_goshow_date() -> None:
    assert goshow_link(POST, CFG) == "https://www.goshow.co.il/show/11534/57425/"
    assert goshow_link("<html><body><p>no iframe</p></body></html>", CFG) is None


def test_event_venue_genres_and_artists() -> None:
    card = next(c for c in parse_board(BOARD, CFG, TZ) if c.post_id == "27904")
    event = build_event(card, goshow_link(POST, CFG), ["ארז בירנבוים", "ערב גסויות"], CFG)
    assert (event.source, event.source_event_id) == ("standupfactory", "27904")
    assert (event.venue_name, event.address, event.city) == ("סטנדאפ פקטורי", "שביל המרץ 5, תל אביב-יפו", "תל אביב-יפו")
    assert (event.latitude, event.longitude) == CLUB
    assert event.extra == {"purchase_url": "https://www.goshow.co.il/show/11534/57425/"}
    assert event.source_genres == ["סטנדאפ", "ערב גסויות"]  # the series is a genre
    assert event.artists == ["ארז בירנבוים"]  # the comedian's category is an artist
    assert event.min_age == 18
    enriched = Enricher(Settings().enrichment, TZ).enrich(event)
    assert enriched is not None and "stand_up" in enriched.genres
    english = build_event(card, None, ["סטנדאפ באנגלית"], CFG)
    enriched = Enricher(Settings().enrichment, TZ).enrich(english)
    assert enriched is not None and {"stand_up", "english"} <= set(enriched.genres)
    assert english.extra == {}


def test_run_reads_board_pages_posts_and_categories() -> None:
    scraper = build_scraper("standupfactory", Settings())
    assert isinstance(scraper, StandupFactoryScraper)
    assert scraper.settings.latitude and scraper.settings.series_categories
    scraper.http.request_delay_seconds = 0
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path, query = request.url.path, dict(request.url.params)
        asked.append(f"{path}?{request.url.query.decode()}" if query else path)
        if path.endswith("/categories"):
            return httpx.Response(200, json=CATEGORIES)
        if path.endswith("/posts"):
            wanted = set(query["include"].split(","))
            return httpx.Response(200, json=[p for p in POSTS if str(p["id"]) in wanted])
        if path == "/לוח-הופעות/":
            # Page 1 is the fixture; later pages are empty here.
            return httpx.Response(200, text=BOARD if "paged" not in query else "<html><body></body></html>")
        return httpx.Response(200, text=POST)

    async def go() -> list[ScrapedEvent]:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return [e async for e in scraper.events()]

    events = asyncio.run(go())
    assert sum(1 for a in asked if a.startswith("/לוח-הופעות/")) == 6  # the board's six pages
    assert not scraper.failures
    starts = [e.start_at.timestamp() for e in events if e.start_at]
    assert starts == sorted(starts)  # nearest first
    by_id = {e.source_event_id: e for e in events}
    # wp-json's permalink replaces the board's ?p= link.
    if "27904" in by_id:
        assert str(by_id["27904"].url).endswith(
            "/%d7%9c%d7%99%d7%a8%d7%95%d7%9f-%d7%90%d7%95%d7%a4%d7%99%d7%a8-%d7%91%d7%9e%d7%95%d7%a4%d7%a2-%d7%a1%d7%98%d7%a0%d7%93%d7%90%d7%a4/"
        )
    if "27192" in by_id:
        assert by_id["27192"].artists == ["ארז בירנבוים"]
    assert all(e.extra.get("purchase_url") for e in events)


def test_cloudflare_refusal_is_a_block() -> None:
    scraper = build_scraper("standupfactory", Settings())
    assert scraper.is_blocked(httpx.Response(202, headers={}, text="<html></html>"))
    assert not scraper.is_blocked(httpx.Response(200, text="<html></html>"))
