"""Ticketmaster Israel: sitemap codes and per-performance parsing on trimmed real API payloads (no network)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from partylinks.models import TierStatus
from partylinks.scrapers.ticketmaster import (
    TicketmasterSettings,
    _status,
    listed_codes,
    parse_event,
    parse_sitemap,
    unwrap,
    upcoming,
    venue_codes,
)

FIXTURES = Path(__file__).parent / "fixtures" / "ticketmaster"
SITE = "https://www.ticketmaster.co.il"
SETTINGS = TicketmasterSettings(
    cron="22 */3 * * *",
    site_url=SITE,
    sitemap_path="/wbtxapi/api/v1/siteMap/event",
    event_path="/wbtxapi/api/v1/bxcached/event/getEventDetail/{code}/INTERNET/iw",
    performances_path="/wbtxapi/api/v1/bxcached/event/getPerformanceList/{code}/INTERNET/iw",
    prices_path="/wbtxapi/api/v1/bxcached/event/getPriceByProfiles/{code}/{perf}/INTERNET/iw",
    performance_page_path="/performance/{code}/{perf}/ALL/iw",
    image_url_template=SITE + "/static/images/live/event/eventimages/{image}",
    wide_image_url_template=SITE + "/static/images/live/event/eventimages/wide/{image}",
)


def load(name: str):
    return unwrap(json.loads((FIXTURES / name).read_text(encoding="utf-8")))


def test_sitemap_codes() -> None:
    assert parse_sitemap((FIXTURES / "sitemap_event.xml").read_text(encoding="utf-8")) == ["MPSE5", "MIO15", "MMM32"]


def test_parse_performance_with_tiers() -> None:
    detail, perfs = load("event_MIO15.json"), load("performances_MIO15.json")
    perf = next(p for p in perfs if p["performanceCode"] == "002")
    event = parse_event(detail, perf, load("prices_MIO15_002.json"), SETTINGS, "Asia/Jerusalem")

    assert event.source_event_id == "MIO15-002"
    assert event.title == "Celtic Throne"
    assert event.start_at is not None and event.start_at.isoformat() == "2026-11-05T20:30:00+02:00"
    assert event.venue_name == "בית האופרה - המשכן לאמנויות הבמה"
    assert event.city == "תל אביב - יפו"
    assert event.latitude and event.longitude
    assert str(event.url) == "https://www.ticketmaster.co.il/performance/MIO15/002/ALL/iw"
    assert event.image_url == SITE + "/static/images/live/event/eventimages/celtic2026_610x336_2026-09-29_152814_4.jpg"
    assert len(event.images) == 2
    assert event.description and "CELTIC THRONE" in event.description
    assert not event.is_sold_out
    # Five public price levels; discount profiles collapse onto the face value.
    tiers = {t.name: t for t in event.tickets}
    assert len(tiers) == 5 and tiers["אולם"].price == Decimal(329)
    assert (event.min_price, event.max_price) == (Decimal(199), Decimal(329))


def test_closed_and_sold_out_performances() -> None:
    detail, perfs = load("event_MIO15.json"), load("performances_MIO15.json")
    closed = parse_event(detail, perfs[0], None, SETTINGS, "Asia/Jerusalem")
    assert closed.tickets[0].status is TierStatus.CLOSED and closed.min_price is None and not closed.is_sold_out

    boy = parse_event(load("event_MMM32.json"), load("performances_MMM32.json")[0], None, SETTINGS, "Asia/Jerusalem")
    assert boy.title == "Boy George" and boy.artists == ["Boy George"]
    assert boy.start_at is not None and boy.start_at.isoformat() == "2026-11-04T21:00:00+02:00"
    assert boy.is_sold_out and boy.tickets[0].price == Decimal(799)


def test_upcoming_skips_past() -> None:
    perfs = load("performances_MIO15.json")
    assert [p["performanceCode"] for p in upcoming(perfs, datetime(2026, 11, 5, 0, 0, tzinfo=UTC))] == ["002", "003"]


def test_codes_beyond_the_sitemap() -> None:
    venues: set[str] = set()
    # Homepage picks: events (with their venue) and groups (no code).
    assert listed_codes(load("top_events.json"), venues) == ["MTA71", "SMM02"]
    assert venues == {"TA", "MM"}
    assert listed_codes(load("spotlight_MUSIC.json"), venues) == ["MRG15"]
    assert "RG" in venues
    # A venue page lists every event playing there (MSH04 Solstars is in no sitemap).
    assert venue_codes(load("venue_SH.json")) == ["MSH07", "MSH01", "MSH02", "MSH04", "MSH05", "MSH06", "MSH08"]


def test_low_availability_still_sells() -> None:
    assert _status("s18_low_availability") is TierStatus.ON_SALE
    assert _status("s13_old_over") is TierStatus.CLOSED
