"""Kzat Tarbut (kzat-tarbut.com): show links in the wp-json posts, performances from kupatbravo.co.il."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.models import ScrapedEvent
from partylinks.scrapers import build_scraper
from partylinks.scrapers.kupatbravo import parse_show
from partylinks.scrapers.kzattarbut import KzatTarbutScraper, KzatTarbutSettings, Show, collect_shows, relabel

TZ = "Asia/Jerusalem"
IL = ZoneInfo(TZ)
# kupatbravo.co.il/announce/87915 (Nov 2026), the page every white label's show id leads to.
ANNOUNCE = (Path(__file__).parent / "fixtures" / "israelshow" / "announce.html").read_text(encoding="utf-8")
SETTINGS = KzatTarbutSettings(cron="29 10,22 * * *")
CATEGORIES = {9: "הצגות", 64: "סטנדאפ", 152: "מגזין"}
POSTS = [
    {
        "id": 2,
        "categories": [64],
        "content": {
            "rendered": '<p><a href="https://5222.kartisim.co.il/announce/87915">כרטיסים</a> '
            '<a href="https://5222.kartisim.co.il/announce/singer-cameri?ref=x">הזמר</a>'
            '<a href="https://5222.kartisim.co.il/">הקופה</a></p>'
        },
    },
    {
        "id": 1,
        "categories": [9, 152],
        "content": {
            "rendered": '<a href="https://5222.kartisim.co.il/announce/singer-cameri">הזמר</a>'
            '<a href="https://5222.kartisim.co.il/announce/%D7%A7%D7%91%D7%A8%D7%98">קברט</a>'
            '<a href="https://5222.kartisim.co.il/announce/קברט">קברט</a>'
        },
    },
]


def test_shows_are_collected_once_with_every_article_category() -> None:
    names = {k: v for k, v in CATEGORIES.items() if v != "מגזין"}  # a section, skipped as the scraper does
    shows = collect_shows(POSTS, names, SETTINGS.show_link)
    assert [(s.key, s.categories) for s in shows] == [
        ("87915", ["סטנדאפ"]),
        ("singer-cameri", ["סטנדאפ", "הצגות"]),
        ("%D7%A7%D7%91%D7%A8%D7%98", ["הצגות"]),  # the encoded and the plain Hebrew slug are one show
    ]


def test_performances_are_relabelled_with_the_white_label_links() -> None:
    bravo = parse_show(ANNOUNCE, "https://kupatbravo.co.il/announce/87915", TZ)
    [event] = relabel(bravo, Show("87915", ["סטנדאפ"]), SETTINGS)
    assert event.source == "kzattarbut"
    assert str(event.url) == "https://5222.kartisim.co.il/announce/87915"
    assert event.extra["purchase_url"] == "https://5222.kartisim.co.il/announce/buy/214861"
    assert event.start_at == datetime(2026, 12, 8, 21, 30, tzinfo=IL)
    assert (event.city, event.latitude, event.longitude) == ("תל אביב-יפו", 32.075088, 34.782006)
    assert event.source_genres == ["סטנדאפ", "סטנד אפ"]
    assert event.images
    enriched = Enricher(Settings().enrichment, TZ).enrich(event)
    assert enriched is not None and "stand_up" in enriched.genres


def test_every_kept_category_is_a_site_genre() -> None:
    enricher = Enricher(Settings().enrichment, TZ)
    skipped = {
        c.casefold() for c in KzatTarbutSettings.model_validate(Settings().sources["kzattarbut"]).skip_categories
    }
    for name in ["הצגות", "הרצאות", "מוסיקה", "משפחה וילדים", "סטנדאפ", "ריקוד ומחול", "תערוכות בישראל"]:
        assert name.casefold() not in skipped
        event = ScrapedEvent(
            source="kzattarbut",
            source_event_id="1",
            url="https://5222.kartisim.co.il/announce/1",
            title="ערב",
            start_at=datetime(2027, 1, 1, 20, tzinfo=IL),
            source_genres=[name],
        )
        enriched = enricher.enrich(event)
        assert enriched is not None and enriched.genres, name


def test_run_reads_posts_then_each_show_on_kupatbravo() -> None:
    scraper = build_scraper("kzattarbut", Settings())
    assert isinstance(scraper, KzatTarbutScraper)
    assert scraper.settings.skip_categories and scraper.settings.skip_title_words
    scraper.http.request_delay_seconds = 0
    soon = f"{datetime.now(IL).date() + timedelta(days=10)}T21:30+02:00"
    announce = ANNOUNCE.replace("2026-12-08T21:30+02:00", soon)
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(f"{request.url.host}{request.url.path}")
        if request.url.path.endswith("/categories"):
            return httpx.Response(200, json=[{"id": k, "name": v} for k, v in CATEGORIES.items()])
        if request.url.path.endswith("/posts"):
            return httpx.Response(200, json=POSTS, headers={"X-WP-Total": str(len(POSTS))})
        if request.url.path == "/announce/87915":
            return httpx.Response(200, text=announce)
        return httpx.Response(200, text='<html><h1 itemprop="name">ישן</h1></html>')  # a show with nothing left

    async def go() -> list[ScrapedEvent]:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return [e async for e in scraper.events()]

    events = asyncio.run(go())
    assert "5222.kartisim.co.il" not in " ".join(asked)  # its robots.txt disallows all
    assert {a for a in asked if a.startswith("kupatbravo")} == {  # httpx gives the path decoded
        "kupatbravo.co.il/announce/87915",
        "kupatbravo.co.il/announce/singer-cameri",
        "kupatbravo.co.il/announce/קברט",
    }
    assert [e.extra["purchase_url"] for e in events] == ["https://5222.kartisim.co.il/announce/buy/214861"]
    assert events[0].source_genres[0] == "סטנדאפ"
    assert not scraper.failures
