"""Meetup: GraphQL event nodes to ScrapedEvent."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from partylinks.scrapers.meetup import parse_event

TZ = "Asia/Jerusalem"
PAGE = json.loads((Path(__file__).parent / "fixtures/meetup/page.json").read_text("utf-8"))
NODES = {e["node"]["id"]: e["node"] for e in PAGE["data"]["recommendedEvents"]["edges"]}


def test_free_tech_meetup() -> None:
    e = parse_event(NODES["316958546"], TZ)
    assert e is not None and e.title == "Core C++ 2026 :: Lightning talks meetup"
    assert e.url.unicode_string() == "https://www.meetup.com/corecpp/events/316958546/"
    assert e.start_at.isoformat() == "2026-10-21T17:00:00+03:00" and e.end_at is not None
    assert e.venue_name == "Tel Aviv Cinematheque" and e.city == "Tel Aviv-Yafo" and e.latitude
    assert e.source_genres == ["hi-tech"] and e.organizer == "Core C++"
    assert e.min_price == 0 and e.tickets[0].kind.value == "free" and e.images


def test_venue_named_for_its_street_and_a_fee() -> None:
    assert parse_event(NODES["316692733"], TZ).venue_name is None  # "Yitzhak Sadeh St 6"
    paid = parse_event(NODES["316935392"], TZ)
    assert paid.min_price == Decimal(65) and paid.currency == "EUR" and paid.tickets[0].kind.value == "paid"


def test_abroad_and_unplaced_events_are_dropped() -> None:
    assert parse_event(NODES["316867243"], TZ) is None  # Amman
    assert parse_event(NODES["316801496"], TZ) is None  # no venue
