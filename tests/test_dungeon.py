"""The Dungeon: WooCommerce Store API products, the promoters' ones left out."""

from __future__ import annotations

from datetime import date, time
from decimal import Decimal
from typing import Any

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.scrapers.dungeon import DungeonSettings, night_of, opening, parse_product

CFG = DungeonSettings(cron="0 0 * * *", latitude=32.0687, longitude=34.7867)
TODAY = date(2026, 11, 24)

PRODUCT: dict[str, Any] = {
    "id": 24738,
    "name": "Dungeon Friday 27.11",
    "slug": "dungeon-friday-27-11",
    "permalink": "https://dungeon.co.il/product/dungeon-friday-27-11/",
    "description": "<p><strong>השבוע בדאנג'ן:</strong></p><p>שישי 27.11<br />פתיחה בחצות<br />Dungeon Friday<br />"
    "על הבמה משחקים קינקיים והופעות פטיש כל הלילה</p>",
    "categories": [],
    "prices": {"price": "18000", "regular_price": "20000", "currency_code": "ILS", "currency_minor_unit": 2},
    "images": [{"src": "https://dungeon.co.il/wp-content/uploads/2026/10/27-11-26.jpg"}],
    "is_in_stock": True,
}


def test_public_product() -> None:
    event = parse_product(PRODUCT, CFG, "Asia/Jerusalem", TODAY)
    assert event is not None
    assert event.start_at is not None
    assert event.start_at.isoformat() == "2026-11-27T23:59:00+02:00"
    assert event.time_known
    assert (event.venue_name, event.address, event.city) == ("הדאנג'ן", "הארבעה 10", "תל אביב")
    assert (event.min_price, event.max_price) == (Decimal(180), Decimal(200))
    assert [t.name for t in event.tickets] == ["מכירה מוקדמת", "מחיר מלא"]
    assert event.min_age == 18
    assert not event.is_sold_out
    assert [str(i) for i in event.images] == ["https://dungeon.co.il/wp-content/uploads/2026/10/27-11-26.jpg"]
    settings = Settings()
    assert "kink" in Enricher(settings.enrichment, settings.timezone)._detect_genres(event)


def test_promoters_and_coming_soon() -> None:
    assert parse_product(PRODUCT | {"categories": [{"name": "יחצנים"}]}, CFG, "Asia/Jerusalem", TODAY) is None
    soon = PRODUCT | {
        "name": "Silvester Fetish Party 31.12",
        "description": "<p>פרטים בקרוב.</p>",
        "is_in_stock": False,
    }
    event = parse_product(soon, CFG, "Asia/Jerusalem", TODAY)
    assert event is not None and event.start_at is not None
    assert event.start_at.date() == date(2026, 12, 31)
    assert not event.time_known
    assert event.is_sold_out
    assert parse_product(PRODUCT | {"name": "Dungeon Friday"}, CFG, "Asia/Jerusalem", TODAY) is None


def test_night_and_hour() -> None:
    assert night_of("Christmas Fetish Party 24.12", TODAY) == date(2026, 12, 24)
    assert night_of("New Year 2.1", TODAY) == date(2027, 1, 2)
    assert night_of("Kimberli's Secret Femdom 19.11", TODAY) == date(2026, 11, 19)  # last week: past, not next year
    assert night_of("Cirque du Kink 31.2", TODAY) is None
    assert opening("חמישי 26.11 פתיחה בשעה 23:30") == time(23, 30)
    assert opening("שישי 27.11 פתיחה בחצות") == time(23, 59)
    assert opening("פרטים בקרוב.") is None
