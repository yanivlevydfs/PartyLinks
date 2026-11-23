"""Ganei Yehoshua (park.co.il): the events listing + each event's page -> one event per session.

Fixtures (23.11.2026), trimmed to the parts read: the listing's two filter menus and six of its cards
(Lumagica, the lake sail, the free Pilates class, an undated exhibition, aerial yoga, a residents-only
session), and the pages of Lumagica (sold on Kupat Tel Aviv), the lake sail (on-site tickets, two times a
day) and aerial yoga (one sold-out time).
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
from partylinks.scrapers.ganeyyehoshua import (
    GaneyYehoshuaScraper,
    GaneyYehoshuaSettings,
    ParkCard,
    parse_event_page,
    parse_listing,
    parse_sessions,
)

TZ = "Asia/Jerusalem"
IL = ZoneInfo(TZ)
FIXTURES = Path(__file__).parent / "fixtures" / "ganeyyehoshua"
SITE = "https://park.co.il"
LISTING = (FIXTURES / "listing.html").read_text("utf-8")
ROSH_TZIPOR = (32.0945101, 34.8049063)
CFG = GaneyYehoshuaSettings.model_validate({
    "cron": "0 0 * * *",
    "places": [
        {"match": ["ראש ציפור", "rosh tsipor"], "venue_name": "מרכז הצפרות ראש ציפור",
         "latitude": ROSH_TZIPOR[0], "longitude": ROSH_TZIPOR[1]},
        {"match": ["ירקון מזרח"], "venue_name": "טיילת ירקון מזרח", "latitude": 32.1077132, "longitude": 34.8379446},
        {"match": ["אמפי תל", "רוקח 100"], "venue_name": "גני יהושע - מתחם אמפי", "latitude": 32.1021103,
         "longitude": 34.8116371},
    ],
    "parks": {"גני יהושע": {"latitude": 32.1030349, "longitude": 34.8167266}},
    "type_genres": {"animals": ["טבע"]},
})  # fmt: skip


def _cards() -> tuple[dict[str, ParkCard], dict[str, str]]:
    cards, types = parse_listing(LISTING, SITE, TZ)
    return {c.post_id: c for c in cards}, types


def _events(post_id: str, page: str) -> list[ScrapedEvent]:
    cards, types = _cards()
    return parse_event_page((FIXTURES / page).read_text("utf-8"), cards[post_id], types, CFG, TZ)


def test_listing_cards_types_and_parks() -> None:
    cards, types = _cards()
    assert types == {
        "animals": "בעלי חיים",
        "nature": "טבע וקיימות",
        "music": "מוזיקה",
        "sport": "ספורט ואתגר",
        "art": "תרבות ואמנות",
    }
    assert set(cards) == {"75009", "69062", "49468", "66566", "78710", "72173"}
    lumagica = cards["75009"]
    assert (lumagica.title, lumagica.park, lumagica.event_type) == ("lumagica תל אביב", "גני יהושע", "art")
    assert lumagica.subtitle == "פסטיבל האור הבינלאומי"
    assert lumagica.url == "https://park.co.il/events/lumagica/"
    # The widest srcset candidate (the upload itself), Hebrew file name percent-encoded.
    assert lumagica.image and lumagica.image.startswith("https://park.co.il/wp-content/uploads/2025/10/%D7%93")
    assert lumagica.image.endswith("-jpg-1.jpg")
    # The card shows the first two dates (the rest in a popover); the page lists them all.
    assert lumagica.sessions == [datetime(2026, 11, 24, 17, 0, tzinfo=IL), datetime(2026, 11, 25, 17, 0, tzinfo=IL)]
    assert cards["66566"].sessions == []  # the open-air exhibition has no dates: no event


def test_sessions_with_two_times_a_day() -> None:
    assert parse_sessions("05.12.26 | 09:00, 11:00 26.12.26 | 09:30", TZ) == [
        datetime(2026, 12, 5, 9, 0, tzinfo=IL),
        datetime(2026, 12, 5, 11, 0, tzinfo=IL),
        datetime(2026, 12, 26, 9, 30, tzinfo=IL),
    ]
    assert parse_sessions("24.11.26 17:00 25.11.26 17:00", TZ) == [
        datetime(2026, 11, 24, 17, 0, tzinfo=IL),
        datetime(2026, 11, 25, 17, 0, tzinfo=IL),
    ]


def test_on_site_tickets_and_a_birdwatching_spot() -> None:
    events = _events("69062", "yarkon-sail.html")
    assert [e.start_at for e in events] == [
        datetime(2026, 12, 5, 9, 0, tzinfo=IL),
        datetime(2026, 12, 5, 11, 0, tzinfo=IL),
    ]
    sail = events[0]
    assert (sail.source, sail.source_event_id) == ("ganeyyehoshua", "69062:202612050900")
    assert sail.title == "שטים עם ציפורים באגם"
    assert str(sail.url) == "https://park.co.il/events/yarkon-sail/"
    # "היכן? ראש ציפור - פארק הצפרות של תל אביב-יפו" -> the birdwatching centre's pin.
    assert (sail.venue_name, sail.city) == ("מרכז הצפרות ראש ציפור", "תל אביב-יפו")
    assert sail.address == "ראש ציפור - פארק הצפרות של תל אביב-יפו"
    assert (sail.latitude, sail.longitude) == ROSH_TZIPOR
    assert [(t.name, t.price) for t in sail.tickets] == [("מחיר לכרטיס", Decimal(60)), ("דיגיתל", Decimal(40))]
    assert (sail.min_price, sail.max_price) == (Decimal(40), Decimal(60))
    assert "purchase_url" not in sail.extra  # sold on the site itself (the event page)
    assert sail.extra["directions"].startswith("ניתן לחנות בחניון החווה החקלאית")
    assert sail.source_genres == ["בעלי חיים", "טבע"]
    assert sail.description and "שייט רגוע באגם" in sail.description and "היכן?" not in sail.description
    assert sail.images and sail.images[0].startswith("https://park.co.il/wp-content/uploads/")
    assert not sail.is_sold_out
    enriched = Enricher(Settings().enrichment, TZ).enrich(sail)
    assert enriched is not None and "environment" in enriched.genres


def test_sold_elsewhere_every_date_from_the_page() -> None:
    events = _events("75009", "lumagica.html")
    assert len(events) == 8  # the page's eight dates, not the card's two
    assert events[-1].start_at == datetime(2026, 12, 5, 18, 0, tzinfo=IL)
    show = events[0]
    assert show.extra["purchase_url"] == "https://www.kupat.co.il/lumagica"
    assert show.tickets == [] and show.min_price is None
    assert (show.venue_name, show.latitude, show.longitude) == ("גני יהושע - מתחם אמפי", 32.1021103, 34.8116371)
    assert show.source_genres == ["תרבות ואמנות"]
    enriched = Enricher(Settings().enrichment, TZ).enrich(show)
    assert enriched is not None and {"culture", "exhibitions"} <= set(enriched.genres)


def test_sold_out_time() -> None:
    events = {e.start_at: e for e in _events("78710", "aerial-yoga.html")}
    assert events[datetime(2026, 11, 27, 10, 0, tzinfo=IL)].is_sold_out
    assert not events[datetime(2026, 11, 27, 11, 0, tzinfo=IL)].is_sold_out
    yoga = events[datetime(2026, 11, 27, 11, 0, tzinfo=IL)]
    assert yoga.venue_name == "טיילת ירקון מזרח"
    assert yoga.source_genres == ["ספורט ואתגר"]


def test_run_reads_the_listing_then_each_dated_event() -> None:
    scraper = build_scraper("ganeyyehoshua", Settings())
    assert isinstance(scraper, GaneyYehoshuaScraper)
    s = scraper.settings
    assert s.places and s.parks and s.skip_title_words and "דיגיטף" in s.skip_title_words
    scraper.http.request_delay_seconds = 0
    pages = {
        "/events/lumagica/": "lumagica.html",
        "/events/yarkon-sail/": "yarkon-sail.html",
        "/events/aerial-yoga/": "aerial-yoga.html",
    }
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        asked.append(path)
        if path == "/אירועים/":
            return httpx.Response(200, text=LISTING)
        if path in pages:
            return httpx.Response(200, text=(FIXTURES / pages[path]).read_text("utf-8"))
        return httpx.Response(200, text="<html><body></body></html>")  # the card's own dates

    async def go() -> list[ScrapedEvent]:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return [e async for e in scraper.events()]

    events = asyncio.run(go())
    assert asked[0] == "/אירועים/"
    assert "/events/plaot/" not in asked and not any("66566" in a for a in asked)  # undated: no page fetched
    starts = [e.start_at.timestamp() for e in events if e.start_at]
    assert starts == sorted(starts)  # nearest first
    # Titles with skip words (the Pilates class, the residents-only session) are not events.
    assert not any("פילאטיס" in e.title or "דיגיטף" in e.title for e in events)
    assert {e.source_event_id.split(":")[0] for e in events} <= {"75009", "69062", "78710"}
    assert not scraper.failures
