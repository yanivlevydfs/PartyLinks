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


GROUPS = json.loads((Path(__file__).parent / "fixtures/meetup/groups.json").read_text("utf-8"))
GROUP = json.loads((Path(__file__).parent / "fixtures/meetup/group.json").read_text("utf-8"))
CATEGORIES = {"data": {"topicCategories": {"edges": [{"node": {"id": "535", "urlkey": "games"}}]}}}


def test_run_reads_the_groups_the_event_search_and_each_long_group_list_once() -> None:
    scraper = build_scraper("meetup", Settings())
    scraper.http.request_delay_seconds = 0
    asked: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        field = next(
            f for f in ("topicCategories", "groupSearch", "recommendedEvents", "groupByUrlname") if f in body["query"]
        )
        asked.append((field, body.get("variables") or {}))
        pages = {"topicCategories": CATEGORIES, "groupSearch": GROUPS, "groupByUrlname": GROUP}
        return httpx.Response(200, json=pages.get(field, PAGE))  # the same search page every time: kept once

    async def go() -> list:
        async with scraper:
            scraper._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            return [e async for e in scraper.events()]

    events = asyncio.run(go())
    s = scraper.settings
    fields = [f for f, _ in asked]
    points = 1 + len(s.centers)
    windows = -(-s.days_ahead // s.window_days)
    assert fields[:2] == ["topicCategories", "groupSearch"] and asked[1][1]["category"] == "535"
    assert fields.count("recommendedEvents") == points * windows
    search = [v for f, v in asked if f == "recommendedEvents"]
    # Window by window, the wide Tel Aviv search first in each.
    assert [(a["lat"], a["radius"]) for a in search[:points]] == [(32.07, 150), (32.8, 30), (31.25, 30), (31.78, 20)]
    first, second = datetime.fromisoformat(search[0]["start"]), datetime.fromisoformat(search[points]["start"])
    assert second - first == timedelta(days=s.window_days)
    assert datetime.fromisoformat(search[0]["end"]) == second
    assert first.date() == datetime.now(zone(TZ)).date()
    # The chess club's list goes on past its first page, and each group met only by the event search
    # is read whole: once each.
    groups = [v["urlname"] for f, v in asked if f == "groupByUrlname"]
    assert groups[0].endswith("chess-tel-aviv-️") and len(groups) == len(set(groups)) == 1 + 3
    ids = [e.source_event_id for e in events]
    assert ids and len(ids) == len(set(ids))
    assert {"317520097", "317285900", "jklsstyjcqbdc", "316958546"} <= set(ids)  # groups, group list, search
