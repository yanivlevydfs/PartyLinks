"""Bimot Global (bimotglobal.co.il): the wp-json show list, the show-type names, and each show's page."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.models import ScrapedEvent
from partylinks.scrapers import build_scraper
from partylinks.scrapers.bimot import BimotScraper, BimotSettings, fold, parse_show

TZ = "Asia/Jerusalem"
IL = ZoneInfo(TZ)
PLACES = {
    "האופרה הישראלית תל אביב": {
        "venue_name": "האופרה הישראלית",
        "city": "תל אביב",
        "address": "שדרות שאול המלך 19, תל אביב",
        "latitude": 32.0763543,
        "longitude": 34.7847576,
    },
    "אודיטוריום חיפה": {
        "venue_name": "אודיטוריום חיפה",
        "city": "חיפה",
        "latitude": 32.8034907,
        "longitude": 34.9850623,
    },
}
SETTINGS = BimotSettings(cron="47 10,22 * * *", places=PLACES)

# The parts of a real show page (עֶרְגָּה, Nov 2026) the scraper reads, as the site writes them.
PAGE = """<html><head>
<meta property="og:image" content="https://bimotglobal.co.il/wp-content/uploads/2026/07/ערגה-להקת-המחול-קמע.webp" />
</head><body>
<div class="elementor-widget-heading"><div class="elementor-heading-title elementor-size-default">להקת המחול קמע</div></div>
<div class="elementor-widget-theme-post-title"><h1 class="elementor-heading-title elementor-size-default">עֶרְגָּה מאת תמיר גינץ</h1></div>
<div class="elementor-shortcode"><div class="show-dates-container"><div class="swiper show-dates-swiper"><div class="swiper-wrapper">
<div class="swiper-slide show-date-entry"><p class="show-date-date">14/12/2026</p><p class="show-date-details">שני | 20:00</p>
<p class="ticket-notice">האופרה הישראלית תל-אביב</p><div class="carousel-bttn-wrapper">
<a href="https://tickets.israel-opera.co.il/order/13286" class="carousel-ticket-bttn" target="_blank">לרכישת כרטיסים</a></div></div>
<div class="swiper-slide show-date-entry"><p class="show-date-date">22/12/2026</p><p class="show-date-details">שלישי | 20:30</p>
<p class="ticket-notice">אודיטוריום חיפה</p><div class="carousel-bttn-wrapper">
<a href="https://www.barak-tickets.co.il/event/ergah-21995389/" class="carousel-ticket-bttn" target="_blank">לרכישת כרטיסים</a></div></div>
<div class="swiper-slide show-date-entry"><p class="show-date-date">05/01/2027</p><p class="show-date-details">שלישי</p>
<p class="ticket-notice">היכל התרבות נתניה</p></div>
<div class="swiper-slide show-date-entry"><p class="show-date-date">בקרוב</p><p class="show-date-details">-</p></div>
</div></div></div></div>
<div class="elementor-widget-text-editor"><p>היצירה נובעת ממקום אישי ופרטי.</p></div>
<div class="elementor-widget-text-editor"><p>מחויבותנו העמוקה ליצירה איכותית</p></div>
<div class="elementor-widget-heading"><div class="elementor-heading-title">תזמורת המהפכה | להקת הבית למחול</div></div>
</body></html>"""
SHOW = {"id": 22934, "link": "https://bimotglobal.co.il/show/ergah/", "title": {"rendered": "עֶרְגָּה"}, "show_type": [24]}
TYPES = {24: "מחול", 23: "אופרה"}


def test_each_dated_slot_is_a_performance_at_its_pinned_hall() -> None:
    events = parse_show(SHOW, PAGE, TYPES, SETTINGS, TZ)
    assert [e.start_at for e in events] == [
        datetime(2026, 12, 14, 20, 0, tzinfo=IL),
        datetime(2026, 12, 22, 20, 30, tzinfo=IL),
        datetime(2027, 1, 5, 0, 0, tzinfo=IL),
    ]
    opera, haifa, netanya = events
    assert opera.title == "עֶרְגָּה מאת תמיר גינץ"
    assert opera.source_event_id == "22934-202612142000"
    assert str(opera.url) == "https://bimotglobal.co.il/show/ergah/"
    assert (opera.venue_name, opera.city, opera.address) == (
        "האופרה הישראלית",
        "תל אביב",
        "שדרות שאול המלך 19, תל אביב",
    )
    assert (opera.latitude, opera.longitude) == (32.0763543, 34.7847576)
    assert opera.extra == {
        "purchase_url": "https://tickets.israel-opera.co.il/order/13286",
        "hall": "האופרה הישראלית תל-אביב",
        "performer": "להקת המחול קמע",
    }
    assert opera.source_genres == ["מחול"]
    assert opera.images == [
        "https://bimotglobal.co.il/wp-content/uploads/2026/07/%D7%A2%D7%A8%D7%92%D7%94-%D7%9C%D7%94%D7%A7%D7%AA-%D7%94%D7%9E%D7%97%D7%95%D7%9C-%D7%A7%D7%9E%D7%A2.webp"
    ]
    assert opera.description == "היצירה נובעת ממקום אישי ופרטי."
    assert opera.time_known
    # The hall that is its own venue keeps no "hall"; its address comes from the pin.
    assert (haifa.venue_name, haifa.city, haifa.latitude) == ("אודיטוריום חיפה", "חיפה", 32.8034907)
    assert "hall" not in haifa.extra
    assert haifa.extra["purchase_url"] == "https://www.barak-tickets.co.il/event/ergah-21995389/"
    assert haifa.address == "אודיטוריום חיפה"  # the city already in the name is not added twice
    # An unpinned hall: its own name, the city found in it, geocoded by the address; no time, no seller.
    assert (netanya.venue_name, netanya.city, netanya.latitude) == ("היכל התרבות נתניה", None, None)
    assert not netanya.time_known
    assert "purchase_url" not in netanya.extra


def test_city_from_an_unpinned_hall_and_folded_hall_names() -> None:
    settings = BimotSettings(cron="47 10,22 * * *", places=PLACES, cities=["נתניה", "תל אביב"])
    netanya = parse_show(SHOW, PAGE, TYPES, settings, TZ)[2]
    assert (netanya.city, netanya.address) == ("נתניה", "היכל התרבות נתניה")
    assert fold("האופרה הישראלית  תל-אביב") == fold("האופרה הישראלית תל אביב")


def test_a_show_without_dates_or_title_gives_nothing() -> None:
    assert parse_show(SHOW, "<html><h1>כרמינה בורנה</h1></html>", TYPES, SETTINGS, TZ) == []
    assert (
        parse_show({"id": 1, "link": "https://bimotglobal.co.il/show/x/"}, "<html></html>", TYPES, SETTINGS, TZ) == []
    )


def test_every_show_type_is_a_site_genre() -> None:
    enricher = Enricher(Settings().enrichment, TZ)
    expected = {
        "אופרה": "classical_opera",
        "מוזיקה": "live_show",
        "מופעי ילדים": "kids",
        "מחול": "dance",
        "מחזמר": "musicals",
        "קרקס": "circus_magic",
        "תאטרון": "theatre",
    }
    for site_type, genre in expected.items():
        event = ScrapedEvent(
            source="bimot",
            source_event_id="1",
            url="https://bimotglobal.co.il/show/x/",
            title="ערב",
            start_at=datetime(2027, 1, 1, 20, tzinfo=IL),
            source_genres=[site_type],
        )
        enriched = enricher.enrich(event)
        assert enriched is not None and genre in enriched.genres, site_type


def test_venue_name_alone_brings_no_opera_genre() -> None:
    """The Opera house as the venue is not an opera: the source keeps only the site's own types."""
    event = parse_show(SHOW, PAGE, TYPES, SETTINGS, TZ)[0]
    enriched = Enricher(Settings().enrichment, TZ).enrich(event)
    assert enriched is not None and enriched.genres == ["dance"]


def test_run_lists_current_shows_and_reads_their_pages() -> None:
    scraper = build_scraper("bimot", Settings())
    assert isinstance(scraper, BimotScraper)
    assert scraper.settings.places and scraper.settings.skip_title_words
    scraper.http.request_delay_seconds = 0
    # The page's dates moved to the coming weeks, so the run keeps them whenever the test runs.
    today = datetime.now(IL).date()
    page = PAGE
    for days, date in ((20, "14/12/2026"), (28, "22/12/2026"), (42, "05/01/2027")):
        page = page.replace(date, f"{today + timedelta(days=days):%d/%m/%Y}")
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path == "/wp-json/wp/v2/show":
            assert request.url.params["season_exclude"] == "49"
            return httpx.Response(200, json=[SHOW], headers={"X-WP-Total": "1"})
        if request.url.path == "/wp-json/wp/v2/show_type":
            return httpx.Response(200, json=[{"id": 24, "name": "מחול"}])
        return httpx.Response(200, text=page)

    async def go() -> list[ScrapedEvent]:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return [e async for e in scraper.events()]

    events = asyncio.run(go())
    assert sorted(asked) == [
        "/show/ergah/",
        "/wp-json/wp/v2/show",
        "/wp-json/wp/v2/show_type",
    ]  # 1 + 1 + a page per show
    starts = [e.start_at.timestamp() for e in events if e.start_at]
    assert len(starts) == 3 and starts == sorted(starts)  # nearest first
    assert [e.venue_name for e in events][:2] == ["האופרה הישראלית", "אודיטוריום חיפה"]
    assert not scraper.failures
