from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from partylinks.config import Settings
from partylinks.enrichment import Enricher
from partylinks.models import EntryType, ScrapedEvent, TicketTier

SETTINGS = Settings()  # loads config/settings.yaml
ENRICHER = Enricher(SETTINGS.enrichment, SETTINGS.timezone)

DESCRIPTION = """אחת המסיבות הכי חמות בעיר.

🎧 LINE UP

MAIN STAGE – MAINSTREAM & EDM:

DOR HADAD
MIA B2B MIKA
w/ SEAN ALONI

📅 חמישי | 22.10
⏰ 22:30

הפתעות כל הלילה עם DJ REYES על הבר.
"""


def make(**overrides: Any) -> ScrapedEvent:
    base: dict[str, Any] = {
        "source": "test",
        "source_event_id": "1",
        "url": "https://example.com/e/1",
        "title": "Techno Thursday",
        "description": DESCRIPTION,
        # 23:30 Thursday in Tel Aviv
        "start_at": datetime(2026, 10, 22, 20, 30, tzinfo=UTC),
        "address": "Rothschild Blvd 24, Tel Aviv-Yafo, Israel",
    }
    return ScrapedEvent(**(base | overrides))


def test_lineup_extracted_from_description() -> None:
    event = ENRICHER.enrich(make())
    assert event is not None
    assert event.lineup == ["DOR HADAD", "MIA B2B MIKA", "SEAN ALONI", "DJ REYES"]


def test_promo_bullets_are_not_a_lineup() -> None:
    promo = "🎶 ליינאפ מטורף\n🌿 לוקיישן קסום\n🌀 סאונד ללא פשרות…\n\n🎧 הליינאפ\n\nברוח היום במה כולה ישראלית 🇮🇱\n"
    event = ENRICHER.enrich(make(description=promo))
    assert event is not None and event.lineup == []


def test_time_categories_in_local_timezone() -> None:
    event = ENRICHER.enrich(make())
    assert event is not None
    assert (str(event.local_date), event.day_of_week, event.time_slot, event.is_weekend) == (
        "2026-10-22", "thursday", "night", True,
    )  # fmt: skip


def test_genres_from_text_and_declared_types() -> None:
    event = ENRICHER.enrich(make(event_types=["house"]))
    assert event is not None
    assert {"techno", "house", "mainstream", "edm"} <= set(event.genres)


def test_entry_type_free_and_paid() -> None:
    tiers = [TicketTier(name="כניסה חינם עד 23:30", price=Decimal(0)), TicketTier(name="Entry", price=Decimal(80))]
    event = ENRICHER.enrich(make(tickets=tiers))
    assert event is not None
    assert event.entry_type is EntryType.FREE_AND_PAID and event.has_free_entry


def test_sold_out_free_tier_is_not_free_entry() -> None:
    tiers = [
        TicketTier(name="להרשמה חינם עד השעה 00:00", price=Decimal(0), is_sold_out=True),
        TicketTier(name="Entry Ticket", price=Decimal(99)),
    ]
    event = ENRICHER.enrich(make(tickets=tiers))
    assert event is not None
    assert event.entry_type is EntryType.PAID and not event.has_free_entry and event.free_sold_out


def test_sold_out_event_is_not_free_entry() -> None:
    tiers = [TicketTier(name="כניסה חינם", price=Decimal(0), is_sold_out=True)]
    event = ENRICHER.enrich(make(tickets=tiers, is_sold_out=True))
    assert event is not None and not event.has_free_entry


def test_entry_type_from_price_range_without_tiers() -> None:
    event = ENRICHER.enrich(make(min_price=Decimal(100), max_price=Decimal(120)))
    assert event is not None and event.entry_type is EntryType.PAID


def test_target_city_matching() -> None:
    assert ENRICHER.enrich(make()).in_target_city is True  # type: ignore[union-attr]
    assert ENRICHER.enrich(make(address="Haifa, Israel")).in_target_city is False  # type: ignore[union-attr]


def test_slugs_are_readable_unique_and_keep_hebrew() -> None:
    a = ENRICHER.enrich(make(title="מסיבת טכנו | Thursday!", venue_name="Moonchild - מון צ'ילד"))
    b = ENRICHER.enrich(make(source_event_id="2", title="מסיבת טכנו | Thursday!"))
    assert a is not None and b is not None
    assert a.slug is not None and a.slug.startswith("מסיבת-טכנו-thursday-2026-10-22-test-")
    assert a.slug != b.slug
    assert a.venue_slug == "moonchild-מון-צ-ילד"


def test_zero_priced_pay_at_door_tier_is_paid_with_price_from_name() -> None:
    tiers = [
        TicketTier(name="‏כרטיס מזומן 120", price=Decimal(0)),
        TicketTier(name="רשומים חינם עד 00:30", price=Decimal(0)),
    ]
    event = ENRICHER.enrich(make(tickets=tiers))
    assert event is not None
    cash, free = event.tickets
    assert cash.pay_at_door and cash.price == Decimal(120) and cash.kind.value == "paid"
    assert free.kind.value == "free" and free.price == 0
    assert event.entry_type is EntryType.FREE_AND_PAID and event.max_price == Decimal(120)


def test_free_at_the_door_stays_free() -> None:
    event = ENRICHER.enrich(make(tickets=[TicketTier(name="חינם בכניסה עד 00:00", price=Decimal(0))]))
    assert event is not None and event.tickets[0].kind.value == "free" and not event.tickets[0].pay_at_door


def test_area_by_keyword_then_coordinates() -> None:
    areas = Enricher(SETTINGS.enrichment, SETTINGS.timezone).areas
    # Keyword in the address wins.
    assert areas.match("בארבי", "קיבוץ גלויות 52, תל אביב-יפו", None, None, True) == "south"
    assert areas.match("Gray Club", "אבן גבירול 30", None, None, True) == "center"
    assert areas.match("Cappella", "Cappella, הארבעה, Tel Aviv-Yafo", 32.0698, 34.786, True) == "business"
    # "Tel Aviv-Yafo" in an address must not mean Jaffa.
    assert areas.match("Humble Club", "Rothschild Blvd 19, Tel Aviv-Yafo", 32.0633, 34.7742, True) == "rothschild"
    # No keyword: nearest center within its radius.
    assert areas.match("Moonchild", "Moonchild, Tel Aviv-Yafo", 32.1002, 34.7745, True) == "north"
    assert areas.match("Somewhere", None, 31.0, 35.0, True) is None
    # Outside the target city.
    assert areas.match("Lobby Night Club", "מקלף 1, חיפה", 32.789, 34.98, False) == "outside"
