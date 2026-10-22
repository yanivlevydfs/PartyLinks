"""Smarticket: tenant /api/shows parsing on a trimmed real Tzavta payload (no network)."""

from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from partylinks.config import HttpSettings
from partylinks.models import ScrapedEvent, TierStatus
from partylinks.parsing import ParseError
from partylinks.scrapers.smarticket import (
    SmarticketScraper,
    SmarticketSettings,
    SmarticketTenant,
    calendar_shows,
    iter_performances,
    parse_event,
    parse_shows_body,
    tenant_host,
)

FIXTURES = Path(__file__).parent / "fixtures" / "smarticket"
TZ = "Asia/Jerusalem"
SETTINGS = SmarticketSettings(
    cron="8 */4 * * *",
    tenant_domain="smarticket.co.il",
    shows_path="/api/shows",
    event_page_path="/event/{id}",
    uploads_path="/uploads/",
    calendar_path="/api/show_theater/get_events_calendar",
    tenants=[
        SmarticketTenant(host="tzavta", venue_name="צוותא", city="תל אביב"),
        SmarticketTenant(host="nosuch", venue_name="Nowhere"),
    ],
)
TZAVTA = SETTINGS.tenants[0]


def _body() -> str:
    return (FIXTURES / "tzavta_shows.json").read_text(encoding="utf-8")


def _events() -> dict[str, ScrapedEvent]:
    pairs = iter_performances(parse_shows_body(_body(), "tzavta.smarticket.co.il"))
    return {e.source_event_id: e for show, p in pairs if (e := parse_event(show, p, TZAVTA, SETTINGS, TZ))}


def test_rooms_tenant_keeps_its_own_venue_name() -> None:
    rooms = TZAVTA.model_copy(update={"rooms": True})
    show, performance = next((s, p) for s, p in iter_performances(parse_shows_body(_body(), "tzavta.smarticket.co.il"))
                             if p["id"] == 17950)  # fmt: skip
    event = parse_event(show, performance, rooms, SETTINGS, TZ)
    assert event is not None and event.venue_name == "צוותא" and event.extra["hall"] == "צוותא 1"


def test_tenant_host() -> None:
    assert tenant_host("tzavta", "smarticket.co.il") == "tzavta.smarticket.co.il"
    assert tenant_host("tickets.raanana.muni.il", "smarticket.co.il") == "tickets.raanana.muni.il"


def test_parse_event() -> None:
    events = _events()
    # The performance marked "ההצגה לא תתקיים" is dropped as cancelled.
    assert "tzavta:18147" not in events and len(events) == 4

    event = events["tzavta:17950"]
    assert event.source == "smarticket"
    assert str(event.url) == "https://tzavta.smarticket.co.il/event/17950"
    assert event.title == "חנוך דאום"
    assert event.start_at is not None
    assert event.start_at.isoformat() == "2026-12-31T21:00:00+02:00"
    assert event.end_at is not None
    assert event.end_at.isoformat() == "2026-12-31T22:15:00+02:00"
    assert (event.venue_name, event.city) == ("צוותא 1", "תל אביב")
    assert event.source_genres == ["בידור"]
    assert event.image_url == "https://tzavta.smarticket.co.il/uploads/upld6a2a85828d46f143136352.png"
    assert [(t.name, t.price, t.status) for t in event.tickets] == [("רגיל", Decimal(149), TierStatus.ON_SALE)]
    assert (event.min_price, event.max_price, event.is_sold_out) == (
        Decimal(149),
        Decimal(149),
        False,
    )

    sold_out = events["tzavta:18126"]  # unavailability_msg "הכרטיסים אזלו"
    assert sold_out.is_sold_out and sold_out.tickets[0].status is TierStatus.SOLD_OUT
    assert sold_out.extra["unavailability_msg"] == "הכרטיסים אזלו"

    no_tickets = events["tzavta:18241"]  # tickets_available false, four tiers, no category
    assert no_tickets.is_sold_out and len(no_tickets.tickets) == 4 and no_tickets.source_genres == []
    assert (no_tickets.min_price, no_tickets.max_price) == (Decimal(85), Decimal(100))


def test_ends_after_midnight_and_fallback_venue() -> None:
    show, performance = iter_performances(parse_shows_body(_body(), "tzavta"))[0]
    performance = {
        **performance,
        "show_time": "23:00",
        "end_time": "01:30",
        "event_place": "",
    }
    event = parse_event(show, performance, TZAVTA, SETTINGS, TZ)
    assert event is not None and event.end_at is not None
    assert event.end_at.isoformat() == "2027-01-01T01:30:00+02:00"
    assert event.venue_name == "צוותא"


def test_unknown_tenant_is_a_config_error() -> None:
    with pytest.raises(ParseError, match="no such Smarticket tenant"):
        parse_shows_body(
            (FIXTURES / "unknown_tenant.html").read_text(encoding="utf-8"),
            "nosuch.smarticket.co.il",
        )


def test_scrape_skips_past_and_isolates_bad_tenant() -> None:
    shows = json.loads(_body())
    future, past = (
        (date.today() + timedelta(days=30)).isoformat(),
        (date.today() - timedelta(days=2)).isoformat(),
    )
    for show in shows:
        for performance in show["events"]:
            performance["show_date"] = past if performance["id"] == 17950 else future
    bodies = {
        "https://tzavta.smarticket.co.il/api/shows": json.dumps(shows, ensure_ascii=False),
        "https://nosuch.smarticket.co.il/api/shows": (FIXTURES / "unknown_tenant.html").read_text(encoding="utf-8"),
    }
    scraper = SmarticketScraper(SETTINGS, HttpSettings(), TZ)

    async def fake_get_text(url: str, **_: object) -> str:
        return bodies[url]

    async def collect() -> list[str]:
        return [e.source_event_id async for e in scraper.scrape()]

    scraper.get_text = fake_get_text  # type: ignore[method-assign]
    assert sorted(asyncio.run(collect())) == [
        "tzavta:18126",
        "tzavta:18241",
        "tzavta:18307",
    ]
    assert len(scraper.failures) == 1 and "no such Smarticket tenant" in scraper.failures[0]


def test_calendar_fallback() -> None:
    """Petah Tikva's /api/shows is []: the calendar feed is read in windows instead."""
    calendar = (FIXTURES / "petah_tikva_calendar.json").read_text(encoding="utf-8")
    shows = calendar_shows(json.loads(calendar)["result"])
    ptk = SmarticketTenant(host="petah-tikva", venue_name="היכל התרבות פתח תקווה", city="פתח תקווה")
    events = [parse_event(show, p, ptk, SETTINGS, TZ) for show, p in iter_performances(shows)]

    first, sold_out = events
    assert first is not None and first.start_at is not None
    assert str(first.url) == "https://petah-tikva.smarticket.co.il/event/10398"
    assert first.title == "מצחיקונת" and first.venue_name == "היכל התרבות פתח תקווה"
    assert first.start_at.isoformat() == "2026-10-20T20:30:00+03:00"
    # Code-only and member-only price rows are dropped.
    assert [t.price for t in first.tickets] == [Decimal(249), Decimal(299)] and not first.is_sold_out
    assert sold_out is not None
    assert sold_out.is_sold_out  # website_left_tickets_count 0

    scraper = SmarticketScraper(SETTINGS.model_copy(update={"tenants": [ptk]}), HttpSettings(), TZ)
    calls: list[dict[str, str]] = []

    async def fake_get_json(url: str, *, params: dict[str, str], **_: object) -> object:
        assert url == "https://petah-tikva.smarticket.co.il/api/show_theater/get_events_calendar"
        calls.append(params)
        return json.loads(calendar)

    scraper.get_json = fake_get_json  # type: ignore[method-assign]
    shows = asyncio.run(scraper._calendar_shows("petah-tikva.smarticket.co.il"))
    assert len(calls) == 2 and calls[1]["start"] > calls[0]["end"]
    assert len(shows) == 2  # same rows in both windows are deduplicated
