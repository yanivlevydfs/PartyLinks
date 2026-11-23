"""IsraelShow (israelshow.co.il): listing cards from its genre pages, performances from kupatbravo.co.il."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.models import ScrapedEvent
from partylinks.scrapers import build_scraper
from partylinks.scrapers.israelshow import Card, IsraelShowScraper, IsraelShowSettings, parse_cards, relabel
from partylinks.scrapers.kupatbravo import parse_show

TZ = "Asia/Jerusalem"
IL = ZoneInfo(TZ)
FIXTURES = Path(__file__).parent / "fixtures" / "israelshow"
LISTING = (FIXTURES / "standup.html").read_text(encoding="utf-8")  # standup.asp, first three cards (Nov 2026)
ANNOUNCE = (FIXTURES / "announce.html").read_text(encoding="utf-8")  # kupatbravo.co.il/announce/87915
SETTINGS = IsraelShowSettings(cron="53 11,23 * * *")


def test_cards_give_the_show_id_and_its_date_range() -> None:
    cards = parse_cards(LISTING, SETTINGS.card_link)
    assert [(c.show_id, c.first, c.last) for c in cards] == [
        ("87915", date(2026, 12, 8), date(2026, 12, 8)),
        ("87914", date(2026, 11, 27), date(2026, 12, 4)),  # written "04.12.2026-27.11.2026"
        ("87898", date(2026, 11, 30), date(2026, 11, 30)),
    ]


def test_a_card_without_a_link_or_date() -> None:
    page = '<ul><li><a href="other.asp">x</a></li><li><a href="https://israelshow.kartisim.co.il/announce/5">y</a></li></ul>'
    assert [(c.show_id, c.first, c.last) for c in parse_cards(page, SETTINGS.card_link)] == [("5", None, None)]


def test_performances_are_relabelled_as_israelshow_with_its_links_and_categories() -> None:
    bravo = parse_show(ANNOUNCE, "https://kupatbravo.co.il/announce/87915", TZ)
    [event] = relabel(bravo, Card("87915", categories=["סטנד אפ ומופעי בידור"]), SETTINGS)
    assert event.source == "israelshow"
    assert str(event.url) == "https://israelshow.kartisim.co.il/announce/87915"
    assert event.extra["purchase_url"] == "https://israelshow.kartisim.co.il/announce/buy/214861"
    assert event.source_event_id == "214861"
    assert event.title == "ליה רוזנפלד – סטנדאפ"
    assert event.start_at == datetime(2026, 12, 8, 21, 30, tzinfo=IL)
    assert (event.city, event.latitude, event.longitude) == ("תל אביב-יפו", 32.075088, 34.782006)
    assert event.venue_name and "בר גיורא" in event.venue_name
    assert event.source_genres == ["סטנד אפ ומופעי בידור", "סטנד אפ"]  # the page's category, then Bravo's
    assert event.images == ["https://katalog.co.il/show/image/87915.jpg?v=4"]
    enriched = Enricher(Settings().enrichment, TZ).enrich(event)
    assert enriched is not None and "stand_up" in enriched.genres


def test_every_page_category_is_a_site_genre() -> None:
    enricher = Enricher(Settings().enrichment, TZ)
    pages = IsraelShowSettings.model_validate(Settings().sources["israelshow"]).pages
    for page, category in pages.items():
        if not category:
            continue
        event = ScrapedEvent(
            source="israelshow",
            source_event_id="1",
            url="https://israelshow.kartisim.co.il/announce/1",
            title="ערב",
            start_at=datetime(2027, 1, 1, 20, tzinfo=IL),
            source_genres=[category.lower()],
        )
        enriched = enricher.enrich(event)
        assert enriched is not None and enriched.genres, (page, category)


def test_run_reads_each_show_once_nearest_first_and_skips_past_cards() -> None:
    scraper = build_scraper("israelshow", Settings())
    assert isinstance(scraper, IsraelShowScraper)
    assert scraper.settings.pages and scraper.settings.skip_title_words
    scraper.settings.pages = {"standup.asp": "סטנד אפ", "comedy-bar.asp": "", "music.asp": "הופעות מוזיקה"}
    scraper.http.request_delay_seconds = 0
    today = datetime.now(IL).date()
    soon, later, past = (f"{today + timedelta(days=d):%d.%m.%Y}" for d in (10, 40, -5))
    show_date = f"{today + timedelta(days=10)}T21:30+02:00"
    announce = ANNOUNCE.replace("2026-12-08T21:30+02:00", show_date)
    assert announce != ANNOUNCE
    card = (
        '<li><br><a href="https://israelshow.kartisim.co.il/announce/{id}"></a><span class="dates">{dates}</span></li>'
    )
    pages = {
        "/standup.asp": card.format(id="87915", dates=soon) + card.format(id="1", dates=past),
        "/comedy-bar.asp": card.format(id="87915", dates=soon) + card.format(id="2", dates=later),
    }
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(f"{request.url.host}{request.url.path}")
        if request.url.host == "israelshow.co.il":
            return httpx.Response(200, text=pages.get(request.url.path, "<html></html>"))
        if request.url.path == "/announce/87915":
            return httpx.Response(200, text=announce)
        return httpx.Response(200, text=announce.replace("214861", "300000"))

    async def go() -> list[ScrapedEvent]:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return [e async for e in scraper.events()]

    events = asyncio.run(go())
    shows = [a for a in asked if a.startswith("kupatbravo")]
    assert shows == [
        "kupatbravo.co.il/announce/87915",
        "kupatbravo.co.il/announce/2",
    ]  # nearest first, the past card not read
    assert "israelshow.kartisim.co.il" not in " ".join(asked)  # its robots.txt disallows all
    assert events[0].source_genres[0] == "סטנד אפ"
    assert {e.extra["purchase_url"] for e in events} == {
        "https://israelshow.kartisim.co.il/announce/buy/214861",
        "https://israelshow.kartisim.co.il/announce/buy/300000",
    }
    assert not scraper.failures
