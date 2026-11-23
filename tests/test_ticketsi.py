"""Ticketsi (ticketsi.co.il): its show calendars (JSON-LD) -> show ids -> kupatbravo.co.il performances.

Fixtures (23.11.2026): three shows' JSON-LD Events from the general calendar ("לוח-הופעות") plus a show only
linked, and Kupat Bravo's page for one of them (the Cameri's "האשליה", show 85945; scripts and styles out).
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.models import ScrapedEvent
from partylinks.scrapers import build_scraper
from partylinks.scrapers.kupatbravo import parse_show
from partylinks.scrapers.ticketsi import Show, TicketsiScraper, TicketsiSettings, parse_calendar, relabel

TZ = "Asia/Jerusalem"
FIXTURES = Path(__file__).parent / "fixtures" / "ticketsi"
CALENDAR = (FIXTURES / "calendar.html").read_text("utf-8")
SHOW = (FIXTURES / "kupatbravo-85945.html").read_text("utf-8")
CFG = TicketsiSettings.model_validate({"cron": "0 0 * * *"})


def test_calendar_show_ids_and_runs() -> None:
    shows = {s.show_id: s for s in parse_calendar(CALENDAR, CFG.show_link)}
    assert set(shows) == {"85945", "85503", "86694", "99999"}
    assert (shows["86694"].first, shows["86694"].last) == (date(2026, 11, 11), date(2026, 12, 1))
    assert shows["99999"].first is None and shows["99999"].last is None  # only linked: no dates


def test_performances_as_ticketsi_sells_them() -> None:
    url = CFG.details_url.format(id="85945")
    events = relabel(parse_show(SHOW, url, TZ), Show("85945", categories=["הצגות תיאטרון"]), CFG)
    assert len(events) == 8
    first = events[0]
    assert (first.source, first.source_event_id) == ("ticketsi", "215091")
    assert str(first.url) == "https://2005.kartisim.co.il/announce/85945"
    assert first.extra["purchase_url"] == "https://2005.kartisim.co.il/announce/buy/215091"
    assert first.start_at == datetime(2026, 11, 23, 20, 30, tzinfo=ZoneInfo(TZ))
    assert first.venue_name == "תיאטרון הקאמרי תל אביב - החדש 3"
    assert first.latitude and first.longitude and first.city
    assert first.source_genres[0] == "הצגות תיאטרון"  # the calendar's category first, then Kupat Bravo's
    enriched = Enricher(Settings().enrichment, TZ).enrich(first)
    assert enriched is not None and "theatre" in enriched.genres


def test_run_reads_calendars_then_each_current_show() -> None:
    scraper = build_scraper("ticketsi", Settings())
    assert isinstance(scraper, TicketsiScraper)
    assert scraper.settings.pages
    scraper.http.request_delay_seconds = 0
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(f"{request.url.host}{request.url.path}")
        if request.url.host == "kupatbravo.co.il":
            return httpx.Response(200, text=SHOW)
        return httpx.Response(200, text=CALENDAR)

    async def go() -> list[ScrapedEvent]:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return [e async for e in scraper.events()]

    events = asyncio.run(go())
    calendars = [a for a in asked if a.startswith("www.ticketsi.co.il")]
    assert len(calendars) == len(scraper.settings.pages)
    shows = [a for a in asked if a.startswith("kupatbravo.co.il")]
    assert len(shows) == len(set(shows)) <= 4  # each show once (runs over before today are skipped)
    assert not scraper.failures
    assert all(e.extra["purchase_url"].startswith("https://2005.kartisim.co.il/announce/buy/") for e in events)
