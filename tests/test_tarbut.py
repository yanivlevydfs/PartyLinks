"""tarbut.events: the admin-ajax events list -> each event's page -> one event per date (a run once).

Fixtures (23.11.2026): one listing page as admin-ajax sends it (10 date cards of 25.11), and three event pages
trimmed to the event block (ads and scripts out): a museum exhibition (weekly runs to 31.12), a children's
stand-up show (one date, Smarticket) and a Cameri play (four dates, each with its own Mevalim link).
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.models import ScrapedEvent
from partylinks.scrapers import build_scraper
from partylinks.scrapers.tarbut import TarbutScraper, TarbutSettings, parse_event_page, parse_listing

TZ = "Asia/Jerusalem"
IL = ZoneInfo(TZ)
FIXTURES = Path(__file__).parent / "fixtures" / "tarbut"
LISTING = (FIXTURES / "listing-page.html").read_text("utf-8")
TODAY = date(2026, 11, 23)
URL = "https://www.tarbut.events/event/x/"
CFG = TarbutSettings.model_validate({
    "cron": "0 0 * * *",
    "region_cities": {"תל אביב-יפו": "תל אביב-יפו"},
    "labels": {"פיסול": ["פיסול", "תערוכה"]},
})  # fmt: skip


def _page(name: str, today: date = TODAY) -> list[ScrapedEvent]:
    return parse_event_page((FIXTURES / name).read_text("utf-8"), "77", URL, CFG, TZ, today)


def test_listing_cards_in_order() -> None:
    cards = parse_listing(LISTING)
    assert len(cards) == 10
    assert cards[0][0] == "7371"
    assert all(url.startswith("https://www.tarbut.events/event/") for _, url in cards)
    assert parse_listing("") == []


def test_play_one_event_per_date_with_its_ticket_link() -> None:
    events = _page("cameri.html")
    assert [e.start_at for e in events] == [
        datetime(2026, 11, 29, 20, 30, tzinfo=IL),
        datetime(2026, 12, 29, 20, 30, tzinfo=IL),
        datetime(2027, 1, 15, 20, 30, tzinfo=IL),
        datetime(2027, 1, 16, 20, 30, tzinfo=IL),
    ]
    play = events[0]
    assert (play.source, play.source_event_id) == ("tarbut", "77:202611292030")
    assert play.title == "סוס אחד נכנס לבר - תיאטרון הקאמרי"
    assert (play.venue_name, play.address, play.city) == ("הקאמרי", "שדרות שאול המלך 19, תל אביב-יפו", "תל אביב-יפו")
    assert (play.latitude, play.longitude) == (32.07606, 34.784977)
    assert play.extra["purchase_url"] == "https://www.mevalim.co.il/event/684799/"
    assert events[1].extra["purchase_url"].startswith("https://tickets.mevalim.co.il/event/")
    assert play.min_price == Decimal(95)
    assert play.source_genres == ["מחזה / דרמה", "הצגה"]
    assert play.images == []  # the plugin's no-event.png placeholder is not artwork
    enriched = Enricher(Settings().enrichment, TZ).enrich(play)
    assert enriched is not None and "theatre" in enriched.genres


def test_exhibition_runs_are_one_event_from_today() -> None:
    events = _page("exhibition.html")
    assert len(events) == 1
    run = events[0]
    assert run.source_event_id == "77:run"
    assert run.start_at == datetime(2026, 11, 23, 0, 0, tzinfo=IL) and not run.time_known
    assert run.end_at == datetime(2026, 12, 31, 23, 59, tzinfo=IL)
    assert run.extra["run"] == ["2026-08-28", "2026-12-31"]
    assert (run.venue_name, run.city) == ("מוזיאון רמת גן לאמנות ישראלית", "רמת גן")
    assert run.latitude and run.longitude
    assert run.description == "אמירה זיאן"
    # "פיסול" maps to nothing of ours: the label adds the exhibition.
    assert run.source_genres == ["אמנות עכשווית", "פיסול", "תערוכה"]
    # A run that is over by then is no event.
    assert _page("exhibition.html", date(2027, 1, 5)) == []


def test_kids_show_ages_and_region() -> None:
    (show,) = _page("kids-standup.html")
    assert show.start_at == datetime(2026, 11, 25, 17, 0, tzinfo=IL)
    assert show.city == "קרית אונו"
    assert show.extra["region"] == "סביב תל אביב"
    assert show.extra["ages"] == ["ילדים", "מבוגרים"]
    assert show.extra["purchase_url"].startswith("https://kono.smarticket.co.il/")
    enriched = Enricher(Settings().enrichment, TZ).enrich(show)
    assert enriched is not None and "stand_up" in enriched.genres


def test_run_reads_pages_until_empty_then_each_event_once() -> None:
    scraper = build_scraper("tarbut", Settings())
    assert isinstance(scraper, TarbutScraper)
    assert scraper.settings.labels and not scraper.settings.proxied
    scraper.http.request_delay_seconds = 0
    pages: list[str] = []
    events_asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            form = dict(x.split("=", 1) for x in request.content.decode().split("&"))
            pages.append(form["page"])
            return httpx.Response(200, text=LISTING if form["page"] in ("1", "2") else "")
        events_asked.append(request.url.path)
        return httpx.Response(200, text=(FIXTURES / "cameri.html").read_text("utf-8"))

    async def go() -> list[ScrapedEvent]:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return [e async for e in scraper.events()]

    events = asyncio.run(go())
    assert sorted(pages, key=int)[:3] == ["1", "2", "3"]  # stops at the first empty page's batch
    assert len(events_asked) == len(set(events_asked)) == 10  # page 2 repeats page 1's events: read once
    assert not scraper.failures
    starts = [e.start_at.timestamp() for e in events if e.start_at]
    assert starts == sorted(starts)


def test_free_badge_and_a_number_for_a_name() -> None:
    page = (FIXTURES / "cameri.html").read_text("utf-8")
    badge = '<span style="background: green;">אירוע חינמי</span></h1>'
    named = page.replace("תיאטרון הקאמרי                     </div>", "תיאטרון הקאמרי " + badge + "</div>", 1)
    free = parse_event_page(named, "77", URL, CFG, TZ, TODAY)[0]
    assert free.title == "סוס אחד נכנס לבר - תיאטרון הקאמרי"  # the badge is not part of the name
    assert free.min_price == 0 and free.tickets and free.tickets[0].is_free
    numbered = page.replace('<h1 class="vp-title">סוס אחד נכנס לבר - תיאטרון הקאמרי', '<h1 class="vp-title">6605110', 1)
    assert parse_event_page(numbered, "77", URL, CFG, TZ, TODAY) == []


def test_a_run_typed_as_a_show_is_an_exhibition() -> None:
    page = (FIXTURES / "exhibition.html").read_text("utf-8").replace('eventtype_id=4">תערוכה', 'eventtype_id=1">הופעה')
    (run,) = parse_event_page(page, "77", URL, CFG, TZ, TODAY)
    assert "הופעה" not in run.source_genres and "תערוכה" in run.source_genres


def test_comedy_beside_a_play_is_a_comedy_play() -> None:
    page = (FIXTURES / "cameri.html").read_text("utf-8").replace('genre_id=11">מחזה / דרמה', 'genre_id=12">קומדיה')
    play = parse_event_page(page, "77", URL, CFG, TZ, TODAY)[0]
    assert play.source_genres == ["מחזה קומי", "הצגה"]
    enriched = Enricher(Settings().enrichment, TZ).enrich(play)
    assert enriched is not None and "theatre" in enriched.genres and "stand_up" not in enriched.genres
    (show,) = _page("kids-standup.html")  # "קומדיה" beside "סטנד־אפ": stand-up as it says
    assert "קומדיה" in show.source_genres
