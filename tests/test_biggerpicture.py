"""BiggerPicture shop (HOT Cinema, Movieland): a screening's regular ticket price, on trimmed real payloads."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from partylinks.scrapers.biggerpicture import parse_event_tiers

FIXTURES = Path(__file__).parent / "fixtures" / "biggerpicture"


def _tiers(name: str) -> list[tuple[str, Decimal | None, Decimal | None, Decimal | None]]:
    payload = json.loads((FIXTURES / name).read_text("utf-8"))
    return [(t.name, t.price, t.service_fee, t.total_price) for t in parse_event_tiers(payload)]


def test_regular_ticket_only_with_online_fee() -> None:
    # Movieland Carmiel: pp 54.5 online, 4.5 of it the fee; concessions (tti 90) need an ID: left out.
    assert _tiers("event_min_1290-116268.json") == [("רגיל", Decimal("50.0"), Decimal("4.5"), Decimal("54.5"))]


def test_vip_hall_price() -> None:
    # HOT Cinema Kfar Saba VIP hall: 88.9 online (p stays 49, the plain hall's box-office price).
    assert _tiers("event_min_vip_1197-159071.json") == [("רגיל", Decimal("84.0"), Decimal("4.9"), Decimal("88.9"))]


def test_no_event() -> None:
    assert parse_event_tiers({"returnCode": 0, "recordCount": 0}) == []
