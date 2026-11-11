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


def _bare(event: dict) -> dict:
    base = {"_id": "6ab27bd8d68cec4957be5ecb", "name": "קמפינג לינת לילה", "location": {"timezone": "Asia/Jerusalem"},
            "schedule": {"start": "2026-11-17 14:00"}}  # fmt: skip
    return {"event": base | event}


def test_no_place_of_its_own_takes_the_producers() -> None:
    # https://www.eventer.co.il/7ag2f (Nov 2026): a campsite's night, no location; its producer is the campsite.
    contact = {"addressDescription": "קיבוץ מעיין צבי, ישראל",
               "address": {"latitude": 32.568484, "longitude": 34.940623, "addressLocality": "מעיין צבי"}}  # fmt: skip
    event = eventer.parse_event(_bare({"producer": {"contactDetails": contact}}), {}, "https://www.eventer.co.il/7ag2f", "UTC")
    assert (event.address, event.city, event.latitude, event.longitude) == (
        "קיבוץ מעיין צבי, ישראל", "מעיין צבי", 32.568484, 34.940623
    )
    # A place of its own wins over the producer's.
    own = _bare({"locationDescription": "הבארבי, תל אביב", "producer": {"contactDetails": contact}})
    event = eventer.parse_event(own, {}, "https://www.eventer.co.il/x", "UTC")
    assert (event.address, event.city, event.latitude) == ("הבארבי, תל אביב", None, None)


def test_blank_place_falls_back_to_its_hebrew_one() -> None:
    # https://www.eventer.co.il/6kl4f (Nov 2026): locationDescription "" but locationDescriptions filled.
    descriptions = {"en_EN": "HaRechavim 13, ירושלים, ישראל", "he_IL": "צוללת צהובה, הרכבים, ירושלים, ישראל"}
    event = eventer.parse_event(
        _bare({"locationDescription": "", "locationDescriptions": descriptions}), {}, "https://www.eventer.co.il/6kl4f", "UTC"
    )
    assert (event.venue_name, event.address) == ("צוללת צהובה", "צוללת צהובה, הרכבים, ירושלים, ישראל")
