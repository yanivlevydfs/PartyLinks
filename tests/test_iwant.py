"""IWANT: listing rows from /events_public.php and the event page's ticket cards."""

from __future__ import annotations

from decimal import Decimal

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.iwant import IwantSettings, parse_event, parse_tiers

ROW = {
    "id": 52,
    "name": "ALL NIGHT LONG חוזרים לפינגווין | NEW WAVE • DARK 80s • EBM",
    "status": "active",
    "date": "2026-11-27",
    "time": "22:00:00",
    "venue": "WAVE CLUB רחוב שבח 1",
    "city": "תל אביב",
    "genre": "שנות ה-80,גל חדש,דארק אייטיז,EBM,סינת'-פופ",
    "image_url": "/uploads/events/event_6ae.jpeg",
    "price": "79.00",
    "sold_out": 0,
    "manual_sold_out": 0,
    "description": "מסיבה",
}

CFG = IwantSettings(cron="0 0 * * *")

PAGE = """<div class="tickets">
<div class="ticket-card " data-price="54" data-name="מחיר מלא"><div><div class="ticket-name">
<span class="early-tag">מוקדמות</span> מחיר מלא </div></div><div class="ticket-price">
<div class="full-price">₪70</div><div class="amount"><span class="currency">₪</span>54</div></div></div>
</div>"""


def test_row() -> None:
    event = parse_event(ROW, CFG, "Asia/Jerusalem")
    assert event is not None
    assert event.source_event_id == "52"
    assert str(event.url) == "https://iwant.co.il/event.php?id=52"
    assert event.start_at.isoformat() == "2026-11-27T22:00:00+02:00"
    assert event.time_known
    assert (event.venue_name, event.city) == ("WAVE CLUB רחוב שבח 1", "תל אביב")
    assert event.source_genres == ["שנות ה-80", "גל חדש", "דארק אייטיז", "ebm", "סינת'-פופ"]
    assert [str(i) for i in event.images] == ["https://iwant.co.il/uploads/events/event_6ae.jpeg"]
    assert event.min_price == Decimal("79.00")
    assert event.extra["purchase_url"] == "https://iwant.co.il/event.php?id=52"
    settings = Settings()
    genres = set(Enricher(settings.enrichment, settings.timezone)._detect_genres(event))
    assert {"throwback", "dark_wave"} <= genres


def test_venue_with_street_other_city_and_inactive() -> None:
    row = ROW | {
        "venue": "📍 Dream Stage – עזריאלי חולון רח' הרוקמים 26, חולון",
        "city": "אחר",
        "genre": "אחר",
        "price": "0.00",
    }
    event = parse_event(row, CFG, "Asia/Jerusalem")
    assert event is not None
    assert event.venue_name == "Dream Stage"
    assert event.city is None
    assert event.address == "Dream Stage – עזריאלי חולון רח' הרוקמים 26, חולון"
    assert event.source_genres == []
    assert event.min_price is None
    assert parse_event(ROW | {"status": "draft"}, CFG, "Asia/Jerusalem") is None


def test_tiers() -> None:
    [tier] = parse_tiers(PAGE)
    assert (tier.name, tier.price, tier.original_price) == ("מחיר מלא", Decimal(54), Decimal(70))
