"""Fever: a plan page's Event JSON-LD (trimmed real page) and its ticket selector's sessions;
a plan without a dated Event is not an event."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from partylinks.config import get_settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.fever import parse_plan

TZ = "Asia/Jerusalem"
URL = "https://feverup.com/m/436641/en"
PAGE = (Path(__file__).parent / "fixtures" / "fever" / "plan_436641.html").read_text(encoding="utf-8")


# Two zones of a sold-out session and one zone of a later session, as the ticket selector writes them
# (the selector repeats its items).
SESSIONS = (
    '{"label":"Zone D","item_type":"1","details":null,"starts_at_iso":"2026-11-17T19:00:00+02:00",'
    '"ends_at_iso":"2026-11-17T20:00:00+02:00","has_available_tickets":false,"price":93.5,"currency":"ILS"}'
    '{"label":"Zone C","item_type":"2","details":null,"starts_at_iso":"2026-11-17T19:00:00+02:00",'
    '"ends_at_iso":"2026-11-17T20:00:00+02:00","has_available_tickets":false,"price":120,"currency":"ILS"}'
    '{"label":"Zone C","item_type":"2","details":null,"starts_at_iso":"2026-11-17T19:00:00+02:00",'
    '"ends_at_iso":"2026-11-17T20:00:00+02:00","has_available_tickets":false,"price":120,"currency":"ILS"}'
    '{"label":"Zone A","item_type":"3","details":null,"starts_at_iso":"2027-01-27T21:00:00+02:00",'
    '"ends_at_iso":"2027-01-27T22:00:00+02:00","has_available_tickets":true,"price":150,"currency":"ILS"}'
)


def test_parse_plan() -> None:
    [event] = parse_plan(PAGE, "436641", URL, TZ)  # no ticket selector: the JSON-LD's next session
    assert (event.source_event_id, event.title) == (
        "436641:2026-12-01T19:00:00+02:00",
        "Candlelight: Vivaldi's Four Seasons",
    )
    assert event.start_at is not None
    assert event.start_at.isoformat() == "2026-12-01T19:00:00+02:00"
    assert (event.venue_name, event.city) == ("Eretz Israel Museum", "Tel Aviv")
    assert event.latitude is not None and event.longitude is not None
    assert (round(event.latitude, 3), round(event.longitude, 3)) == (32.103, 34.796)
    assert event.min_price == Decimal("75.5") and event.tickets
    assert event.images and event.images[0].startswith("https://applications-media.feverup.com/")
    assert event.source_genres == ["candlelight", "classical"]
    assert event.extra["purchase_url"] == URL
    enriched = Enricher(get_settings().enrichment, TZ).enrich(event)
    assert enriched is not None and "classical_opera" in enriched.genres and enriched.city == "תל אביב"


def test_one_event_per_session_of_the_ticket_selector() -> None:
    """The JSON-LD names one session only; each session start in the selector is its own event."""
    first, later = parse_plan(PAGE + f"<script>{SESSIONS}</script>", "436641", URL, TZ)
    assert first.source_event_id == "436641:2026-11-17T19:00:00+02:00"
    assert first.start_at.isoformat() == "2026-11-17T19:00:00+02:00"
    assert first.end_at is not None and first.end_at.isoformat() == "2026-11-17T20:00:00+02:00"
    assert [(t.name, t.price) for t in first.tickets] == [("Zone D", Decimal("93.5")), ("Zone C", Decimal(120))]
    assert (first.min_price, first.max_price, first.is_sold_out) == (Decimal("93.5"), Decimal(120), True)
    assert later.start_at.isoformat() == "2027-01-27T21:00:00+02:00" and not later.is_sold_out
    assert later.min_price == Decimal(150)
    # Place, image and title come from the JSON-LD on every session.
    assert later.venue_name == "Eretz Israel Museum" and later.images == first.images and later.images


def test_a_plan_without_a_dated_event_is_skipped() -> None:
    assert parse_plan("<html><body>a guided tour</body></html>", "1", URL, TZ) == []
