"""Petah Tikva municipality: Umbraco Delivery API rows (7 November 2026) to ScrapedEvent."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.parsing import zone
from partylinks.scrapers.petahtikvamuni import PetahTikvaMuniSettings, parse_event

TZ = "Asia/Jerusalem"
ROWS = {
    i["route"]["path"].split("/")[-2]: i
    for i in json.loads((Path(__file__).parent / "fixtures/petahtikvamuni/events.json").read_text("utf-8"))["items"]
}
S = PetahTikvaMuniSettings(
    cron="0 0 * * *",
    site_url="https://www.petah-tikva.muni.il",
    api_url="https://api.petah-tikva.muni.il",
    city="פתח תקווה",
    venue_addresses={"ספריית מנגינות": "מבצע דקל 3"},
)


def test_smarticket_mirror_and_zoom_lecture_are_skipped() -> None:
    assert parse_event(ROWS["5472"], S, TZ) is None  # makefet.smarticket.co.il, read by smarticket
    assert parse_event(ROWS["how-to-plan-a-city-10-11-2026"], S, TZ) is None  # ZOOM


def test_hall_with_its_address_under_it() -> None:
    e = parse_event(ROWS["holhim-badereh"], S, TZ)
    assert e is not None
    assert e.title.startswith("ההולכים בדרך")
    # "2026-11-10T18:00:00Z" is the Israeli wall clock, not UTC.
    assert e.start_at == datetime(2026, 11, 10, 18, 0, tzinfo=zone(TZ))
    assert e.end_at == datetime(2026, 11, 10, 20, 0, tzinfo=zone(TZ))
    assert (e.venue_name, e.address, e.city) == ("אולם מופת", "הסתדרות 19", "פתח תקווה")
    assert e.extra["purchase_url"] == "https://goactive.naamat.org.il/cart/3531"
    assert (e.min_price, e.tickets[0].is_free) == (Decimal(10), False)
    assert str(e.url) == "https://www.petah-tikva.muni.il/events/all-events/holhim-badereh/"
    assert e.source_genres == ["תיאטרון"]


def test_address_as_place_and_hall_under_it() -> None:
    e = parse_event(ROWS["wine-tasting-workshop-12-11-26"], S, TZ)
    assert e is not None
    assert (e.venue_name, e.address) == ("החממה מרכז צעירים", "אוסישקין 1, פתח תקווה")
    assert e.extra["purchase_url"] == "https://tickchak.co.il/114609"
    assert "סדנאות" in e.source_genres


def test_market_row_gets_its_hall_street_and_image() -> None:
    e = parse_event(ROWS["289350"], S, TZ)
    assert e is not None
    assert (e.venue_name, e.address) == ("ספריית מנגינות", "מבצע דקל 3")
    assert e.extra["purchase_url"].startswith("https://market.petah-tikva.muni.il/product/")
    assert e.image_url and e.image_url.startswith("https://")


def test_course_end_months_later_is_dropped() -> None:
    e = parse_event(ROWS["arabic-course-18-11-26"], S, TZ)
    assert e is not None and e.end_at is None  # 18.11 to 20.1: the last meeting, not this one's end
    assert e.min_price == Decimal(1000)


def test_every_kept_row_gets_a_site_genre() -> None:
    settings = Settings()
    enricher = Enricher(settings.enrichment, settings.timezone)
    for key in ("holhim-badereh", "wine-tasting-workshop-12-11-26", "289350", "arabic-course-18-11-26"):
        e = parse_event(ROWS[key], S, TZ)
        assert e is not None and enricher.enrich(e).genres, key
