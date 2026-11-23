"""Meetup: GraphQL event nodes to ScrapedEvent."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx

from partylinks.config import Settings
from partylinks.parsing import zone
from partylinks.scrapers import build_scraper
from partylinks.scrapers.meetup import parse_event

TZ = "Asia/Jerusalem"
PAGE = json.loads((Path(__file__).parent / "fixtures/meetup/page.json").read_text("utf-8"))
NODES = {e["node"]["id"]: e["node"] for e in PAGE["data"]["recommendedEvents"]["edges"]}


def test_free_tech_meetup() -> None:
    e = parse_event(NODES["316958546"], TZ)
    assert e is not None and e.title == "Core C++ 2026 :: Lightning talks meetup"
    assert e.url.unicode_string() == "https://www.meetup.com/corecpp/events/316958546/"
    assert e.start_at is not None
    assert e.start_at.isoformat() == "2026-10-21T17:00:00+03:00" and e.end_at is not None
    assert e.venue_name == "Tel Aviv Cinematheque" and e.city == "Tel Aviv-Yafo" and e.latitude
    assert e.source_genres == ["hi-tech"] and e.organizer == "Core C++"
    assert e.min_price == 0 and e.tickets[0].kind.value == "free" and e.images


def test_venue_named_for_its_street_and_a_fee() -> None:
    street = parse_event(NODES["316692733"], TZ)
    assert street is not None and street.venue_name is None  # "Yitzhak Sadeh St 6"
    paid = parse_event(NODES["316935392"], TZ)
    assert paid is not None
    assert paid.min_price == Decimal(65) and paid.currency == "EUR" and paid.tickets[0].kind.value == "paid"


def test_abroad_and_unplaced_events_are_dropped() -> None:
    assert parse_event(NODES["316867243"], TZ) is None  # Amman
    assert parse_event(NODES["316801496"], TZ) is None  # no venue


def test_run_asks_each_window_and_point_and_keeps_each_event_once() -> None:

    scraper = build_scraper("meetup", Settings())
    scraper.http.request_delay_seconds = 0
    asked: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        variables = json.loads(request.content)["variables"]
        asked.append(variables)
        return httpx.Response(200, json=PAGE)  # the same page every time: its events are kept once

    async def go() -> list:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return [e async for e in scraper.events()]

    events = asyncio.run(go())
    s = scraper.settings
    points = 1 + len(s.centers)
    windows = -(-s.days_ahead // s.window_days)
    assert points == 4 and len(asked) == points * windows
    # Window by window, the wide Tel Aviv search first in each.
    assert [(a["lat"], a["radius"]) for a in asked[:points]] == [(32.07, 150), (32.8, 30), (31.25, 30), (31.78, 20)]
    first, second = datetime.fromisoformat(asked[0]["start"]), datetime.fromisoformat(asked[points]["start"])
    assert second - first == timedelta(days=s.window_days)
    assert datetime.fromisoformat(asked[0]["end"]) == second
    assert first.date() == datetime.now(zone(TZ)).date()
    ids = [e.source_event_id for e in events]
    assert ids and len(ids) == len(set(ids))
