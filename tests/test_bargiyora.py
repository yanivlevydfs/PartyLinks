"""Bar Giyora parser test against the real first listing page and its Store API products (October 2026)."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from partylinks.config import Settings
from partylinks.scrapers import bargiyora

FIXTURES = Path(__file__).parent / "fixtures" / "bargiyora"
TZ = "Asia/Jerusalem"


def test_bargiyora_listing_and_products() -> None:
    cfg = bargiyora.BarGiyoraSettings.model_validate(Settings().sources["bargiyora"])
    cards = bargiyora.parse_listing((FIXTURES / "tickets.html").read_text(encoding="utf-8"))
    products = {str(p["id"]): p for p in json.loads((FIXTURES / "products.json").read_text(encoding="utf-8"))}
    assert len(cards) == 9

    events = [bargiyora.parse_event(c, products.get(c["id"]), cfg, TZ) for c in cards]
    odyssey = events[0]
    assert odyssey is not None
    assert odyssey.title == "דורון פישלר - האודיסיאה"
    assert odyssey.source_event_id == "36093"
    assert odyssey.start_at == datetime(2026, 10, 26, 18, 30, tzinfo=ZoneInfo(TZ))
    assert odyssey.source_genres == ["הרצאה"]
    assert odyssey.min_price == Decimal(80)
    assert odyssey.venue_name == "בר גיורא" and odyssey.city == "תל אביב-יפו"
    assert odyssey.images and odyssey.images[0].startswith("https://bar-giyora.co.il/wp-content/uploads/")
    assert odyssey.description is None  # empty in the Store API: read from the product page
    page = (FIXTURES / "product.html").read_text(encoding="utf-8")
    assert "אודיסיאוס" in (bargiyora.parse_description(page) or "")
    assert events[1] is not None and events[1].description  # this one the Store API has
    assert [e.is_sold_out for e in events if e and e.source_event_id == "35478"] == [True]

    # No Store API product: the card alone still makes an event, without a price.
    bare = bargiyora.parse_event(cards[0], None, cfg, TZ)
    assert bare is not None and bare.min_price is None and not bare.tickets
    assert bargiyora.parse_event({**cards[0], "date": ""}, None, cfg, TZ) is None
