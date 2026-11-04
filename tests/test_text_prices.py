"""Prices written in event text, on real descriptions (Secret Tel Aviv, Meety, November 2026)."""

from __future__ import annotations

from decimal import Decimal

from partylinks.scrapers.text_prices import text_prices, text_tickets


def test_ticket_amounts_and_free_entry() -> None:
    assert text_prices("ראשון| 29.11 | 21:00 | כרטיס: 40 ₪\n\nמספר המקומות מוגבל | 18+") == [Decimal(40)]
    assert text_prices("Tickets are 70 NIS presale or 80 NIS at the door.") == [Decimal(70), Decimal(80)]
    assert text_prices("🎟️ Early Bird: ₪70 through November 2") == [Decimal(70)]
    assert text_prices('הכניסה בהשתתפות מומלצת של 70-120 ש"ח.') == [Decimal(70), Decimal(120)]
    # A punch card, a series pass, a prize, a minimum spend or goods on sale are not one entry.
    assert text_prices('עלות כרטיס בודד: 30 ש"ח.  300 ש"ח לכרטיסייה של 12 כניסות.') == [Decimal(30)]
    assert text_prices("כניסה חד פעמית עולה 50₪, או כרטיסייה בעלות של 160₪ ל-4 כניסות (סטודנטים 140₪).") == [
        Decimal(50)
    ]
    assert text_prices("כרטיס בודד: 60 ₪ | כרטיס לכל הסדרה: 220 ₪") == [Decimal(60)]
    assert text_prices("הקבוצה הזוכה תזכה בפרס כולל של 200,000 ₪") == []
    assert text_prices("להזמנת מקום ישיבה במינימום של 350₪ לאדם") == []
    assert text_prices("קולקציות חורף 2026/7 במחירים שווים במיוחד - 50-200 ₪!") == []

    free = text_tickets("רביעי | 4.11 | 20:00 דלתות | כניסה חופשית | רבי עקיבא 3, כרם התימנים")
    assert [(t.price, t.is_free) for t in free] == [(Decimal(0), True)]
    assert text_tickets("Doors at 20:00, entry is free.")[0].price == 0
    assert text_tickets("הכניסה לשוק - ללא תשלום. רכישת החפצים - בתשלום.")[0].price == 0
    # Free drinks or free parking are not free entry.
    assert text_tickets("בופה עשיר ושתיה חופשית. חניה בחינם בחניון רידינג") == []
