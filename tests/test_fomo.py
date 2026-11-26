"""FOMO: the public catalogue API and two event details, on a trimmed real answer (26.11.2026)."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.models import TierStatus
from partylinks.scrapers.fomo import FomoSettings, listed, merge, parse_event

FIXTURE = Path(__file__).parent / "fixtures" / "fomo" / "catalogue.json"
TZ = "Asia/Jerusalem"
SETTINGS = FomoSettings(cron="26 9,21 * * *")
CHOSEN = "6af6b25550e62be4c989c89a"
ANDRESKAS = "6afc2ecc6cfaa95cac33a806"


def _data() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _merged(event_id: str) -> dict[str, Any]:
    data = _data()
    (row,) = [r for r in data["listing"]["data"] if r["id"] == event_id]
    return merge(row, data["details"][event_id])


def test_secret_spot_party() -> None:
    event = parse_event(_merged(CHOSEN), SETTINGS, TZ)
    assert event is not None
    assert event.title == "CHOSEN PEOPLE"
    assert event.start_at is not None and event.start_at.isoformat() == "2026-11-26T23:55:00+02:00"
    assert event.end_at is not None and event.end_at.isoformat() == "2026-11-27T13:00:00+02:00"
    # "ישראל" as venue, address and city is no place.
    assert (event.venue_name, event.address, event.city) == (None, None, None)
    assert str(event.url) == "https://www.fomo-events.co/he/event/chosen"
    assert event.extra["purchase_url"] == str(event.url)
    assert event.images == [f"https://www.fomo-events.co/api/media/event/{CHOSEN}?v=mwxzuice"]
    assert event.source_genres == ["music"] and event.min_age == 23
    assert event.organizer == "chosen people"


def test_tickets_in_agorot_with_the_default_fee() -> None:
    event = parse_event(_merged(CHOSEN), SETTINGS, TZ)
    assert event is not None
    first = event.tickets[0]
    assert (first.name, first.price, first.service_fee) == ("השורה הראשונה", Decimal(150), Decimal("7.50"))
    assert first.status is TierStatus.CLOSED  # its sale ended 15.11
    assert any(t.status is TierStatus.ON_SALE for t in event.tickets)
    assert event.min_price == Decimal(0)  # the cash-list ticket


def test_region_and_nature_are_no_venue_and_og_image_is_the_fallback() -> None:
    event = parse_event(_merged(ANDRESKAS), SETTINGS, TZ)
    assert event is not None
    assert (event.venue_name, event.city) == (None, None)  # "צפון", "בטבע היפה שלנו"
    assert event.images == [f"https://www.fomo-events.co/api/events/{ANDRESKAS}/og-image"]
    (tier,) = event.tickets
    assert (tier.price, tier.service_fee, tier.remaining) == (Decimal(100), Decimal(7), 48)  # its own 7% fee


def test_private_and_draft_are_not_listings() -> None:
    row = _merged(ANDRESKAS)
    assert listed(row)
    assert not listed({**row, "isPrivate": True})
    assert parse_event({**row, "status": "DRAFT"}, SETTINGS, TZ) is None
    assert parse_event({**row, "startsAt": None}, SETTINGS, TZ) is None


def test_external_event_links_to_its_seller() -> None:
    row = {**_merged(ANDRESKAS), "isExternal": True, "externalProviderUrl": "https://www.go-out.co/event/x"}
    event = parse_event(row, SETTINGS, TZ)
    assert event is not None and event.extra["purchase_url"] == "https://www.go-out.co/event/x"


def test_enrichment_gives_site_genres() -> None:
    event = parse_event(_merged(ANDRESKAS), SETTINGS, TZ)
    assert event is not None
    enriched = Enricher(Settings().enrichment, TZ).enrich(event)
    assert enriched is not None and enriched.genres
