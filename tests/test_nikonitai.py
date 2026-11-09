"""Niko Nitai Theatre: home-page performance links and the event page's JSON-LD to ScrapedEvent."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from partylinks.config import get_settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.nikonitai import NikoNitaiSettings, parse, performance_ids

PAGE = (Path(__file__).parent / "fixtures/nikonitai/event_620.html").read_text("utf-8")
S = NikoNitaiSettings.model_validate(get_settings().sources["nikonitai"])


def test_links_once_each_and_not_plain_pages() -> None:
    html = '<a href="אחדות/?id=620"> <a href="אחדות/?id=621"> <a href="אחדות/?id=620"> <a href="גלריה_ניקו_ניתאי">'
    assert performance_ids(html) == ["620", "621"]


def test_performance() -> None:
    e = parse(PAGE, "620", S, "Asia/Jerusalem")
    assert e is not None and e.source == "nikonitai" and e.title == "אחדות - פסטיבל האזרח כאן"
    assert e.url.unicode_string() == "https://nikonitai.smarticket.co.il/event/620"
    assert e.start_at is not None and e.start_at.isoformat() == "2026-11-30T21:00:00+02:00"
    assert e.end_at is not None and e.end_at.isoformat() == "2026-11-30T21:30:00+02:00"
    assert e.venue_name == "תיאטרון ניקו ניתאי" and e.latitude and e.longitude and e.city
    assert e.min_price == Decimal(0) and e.images and "//uploads" not in e.images[0].split("://", 1)[1]
    settings = get_settings()
    assert Enricher(settings.enrichment, settings.timezone)._detect_genres(e)


def test_page_without_event_is_skipped() -> None:
    assert parse("<html><title>x</title></html>", "307", S, "Asia/Jerusalem") is None
