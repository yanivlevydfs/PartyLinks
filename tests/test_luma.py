"""Luma: listing page ids and /event/get to ScrapedEvent."""

from __future__ import annotations

import asyncio
import copy
import json
from decimal import Decimal
from pathlib import Path

import httpx

from partylinks.config import Settings
from partylinks.scrapers import build_scraper
from partylinks.scrapers.luma import listed_calendars, listed_ids, parse_event

TZ = "Asia/Jerusalem"
SITE = "https://luma.com"
FIXTURES = Path(__file__).parent / "fixtures/luma"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text("utf-8"))


def test_listing_keeps_in_person_events_in_israel() -> None:
    page = _load("page.json")
    assert list(listed_ids(page)) == ["evt-Kf6PpQNGbjAXA3B", "evt-z1tlnqsA1x4PJMm", "evt-TiPMKw8LyqNa2ZC"]
    page["entries"][0]["event"]["geo_address_info"]["country_code"] = "JO"
    page["entries"][1]["event"]["location_type"] = "online"
    assert list(listed_ids(page)) == ["evt-TiPMKw8LyqNa2ZC"]


def test_free_event_with_shown_address() -> None:
    e = parse_event(_load("evt-Kf6PpQNGbjAXA3B.json"), SITE, TZ)
    assert e is not None and e.url.unicode_string() == "https://luma.com/vhi7dbcl"
    assert e.title == "EcoValue Forum Gathering @ PLANETech Week"
    assert e.venue_name == "בית קפה נורה - Nora Cafe" and e.address == "שלמה אבן גבירול 71" and e.city == "תל אביב-יפו"
    assert e.latitude and abs(e.latitude - 32.0823) < 0.001
    assert e.organizer == "EcoValue Forum"  # personal calendar: the host
    assert e.description and e.description.startswith("A new peer forum") and "\n" in e.description
    assert [t.kind.value for t in e.tickets] == ["free"] and e.min_price == 0
    assert e.event_types == ["Climate"] and e.source_genres == []


def test_paid_event_in_dollars() -> None:
    e = parse_event(_load("evt-80NFgdyIp4er616.json"), SITE, TZ)
    assert e is not None and e.currency == "USD" and e.min_price == Decimal(30)
    assert e.tickets[0].currency == "USD" and e.tickets[0].name == "Standard"


def test_online_event_is_skipped() -> None:
    detail = _load("evt-Kf6PpQNGbjAXA3B.json")
    detail["event"]["location_type"] = "online"
    assert parse_event(detail, SITE, TZ) is None


def test_calendars_of_the_discovery_bring_their_other_events() -> None:
    page = _load("page.json")
    assert listed_calendars(page) == ["cal-D7oEcy8rptiQqHR", "cal-7BBGnCu2rKnKB7L", "cal-G5PQtKn72MSKnlW"]
    # A calendar's entry has its own id ("calev-"); the event inside it is the one to fetch.
    other = copy.deepcopy(page["entries"][0])
    other["api_id"] = "calev-Zz9"
    other["event"]["api_id"], other["event"]["start_at"] = "evt-80NFgdyIp4er616", "2026-10-20T16:00:00.000Z"
    abroad = copy.deepcopy(other)
    abroad["event"]["api_id"], abroad["event"]["geo_address_info"]["country_code"] = "evt-Abroad", "GB"
    calendar_page = {"entries": [page["entries"][0], other, abroad], "has_more": False}
    scraper = build_scraper("luma", Settings())
    scraper.http.request_delay_seconds = 0
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        asked.append(f"{request.url.path}?{params.get('calendar_api_id') or params.get('event_api_id') or ''}")
        if request.url.path == "/discover/get-paginated-events":
            return httpx.Response(200, json=page)
        if request.url.path == "/calendar/get-items":
            assert request.url.params["period"] == "future"
            return httpx.Response(
                200,
                json=calendar_page
                if request.url.params["calendar_api_id"] == "cal-D7oEcy8rptiQqHR"
                else {"entries": []},
            )
        name = request.url.params["event_api_id"]
        return httpx.Response(200, json=_load(f"{name}.json") if (FIXTURES / f"{name}.json").exists() else {})

    async def go() -> list:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return [e async for e in scraper.scrape()]

    asyncio.run(go())
    fetched = [a.split("?")[1] for a in asked if a.startswith("/event/get")]
    # Every listed event once, the calendar's extra one too, nearest first (20.10); not the one abroad.
    assert fetched[0] == "evt-80NFgdyIp4er616"
    assert sorted(fetched) == sorted(["evt-80NFgdyIp4er616", *listed_ids(page)])
    calendars = [a for a in asked if a.startswith("/calendar/get-items")]
    assert len(calendars) == 3  # each calendar once, though every center lists it
