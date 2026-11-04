"""Eventer events that came out without a price (trimmed real payloads, Nov 2026)."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from partylinks.models import TierStatus
from partylinks.scrapers import eventer

CASES = json.loads((Path(__file__).parent / "fixtures" / "eventer" / "no_price.json").read_text(encoding="utf-8"))


def test_every_tier_sold_out_keeps_its_face_value() -> None:
    case = CASES["sold_out"]  # https://www.eventer.co.il/sdz2f: its one tier at price -1, originalPrice 30
    event = eventer.parse_event(case["details"], case["tickets"], "https://www.eventer.co.il/sdz2f", "Asia/Jerusalem")
    assert [(t.price, t.status) for t in event.tickets] == [(Decimal(30), TierStatus.SOLD_OUT)]
    assert event.min_price == event.max_price == Decimal(30) and event.is_sold_out


def test_sales_ended_status_is_sold_out() -> None:
    case = CASES["sales_ended"]  # https://www.eventer.co.il/m012f: status 4, ticketTypes.js answers 403
    event = eventer.parse_event(case["details"], case["tickets"], "https://www.eventer.co.il/m012f", "Asia/Jerusalem")
    assert event.is_sold_out and not event.tickets
