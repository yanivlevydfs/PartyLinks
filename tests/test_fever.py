"""Fever: a plan page's Event JSON-LD (trimmed real page); a plan without one is not an event."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from partylinks.config import get_settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.fever import parse_plan

TZ = "Asia/Jerusalem"
URL = "https://feverup.com/m/436641/en"
PAGE = (Path(__file__).parent / "fixtures" / "fever" / "plan_436641.html").read_text(encoding="utf-8")


def test_parse_plan() -> None:
    event = parse_plan(PAGE, "436641", URL, TZ)
    assert event is not None
    assert (event.source_event_id, event.title) == ("436641", "Candlelight: Vivaldi's Four Seasons")
    assert event.start_at.isoformat() == "2026-12-01T19:00:00+02:00"
    assert (event.venue_name, event.city) == ("Eretz Israel Museum", "Tel Aviv")
    assert (round(event.latitude, 3), round(event.longitude, 3)) == (32.103, 34.796)
    assert event.min_price == Decimal("75.5") and event.tickets
    assert event.images and event.images[0].startswith("https://applications-media.feverup.com/")
    assert event.source_genres == ["candlelight", "classical"]
    assert event.extra["purchase_url"] == URL
    enriched = Enricher(get_settings().enrichment, TZ).enrich(event)
    assert enriched is not None and "classical_opera" in enriched.genres and enriched.city == "תל אביב"


def test_a_plan_without_a_dated_event_is_skipped() -> None:
    assert parse_plan("<html><body>a guided tour</body></html>", "1", URL, TZ) is None
