"""Ticket prices written in an event's free text ("כרטיס: 40 ₪", "Tickets are 70 NIS", "הכניסה חופשית").

For guides that show no price field (Secret Tel Aviv, Meety, ZOA's own text): an amount counts only in a
sentence that talks about tickets / entry / price, and not when its clause is about something else
(a punch card, a prize, a minimum spend). "Free entry" phrases give a price-0 tier.
"""

from __future__ import annotations

import re
from decimal import Decimal

from partylinks.models import TicketTier

_NUM = r"\d{1,3}(?:,\d{3})+|\d+(?:\.\d{1,2})?"
_CUR = r"₪|ש\"ח|ש״ח|ש''ח|\bNIS\b|\bILS\b|shekels?\b"
# "40 ₪", "70-120 ש"ח", "₪70", "NIS 70"
_AMOUNT = re.compile(rf"(?:(?:₪|\bNIS)\s*({_NUM}))|(?:({_NUM})(?:\s*[-–]\s*({_NUM}))?\s*(?:{_CUR}))", re.IGNORECASE)
_SENTENCE = re.compile(r"\n|\||[!?]|\.(?!\d)")
_TICKET_WORDS = re.compile(
    r"כרטיס|כניס|מחיר(?!ים)|עלות|השתתפות|הרשמה|למשתתף|לאורח|דמי|מוקדמ"
    r"|ticket|entry|entrance|admission|price(?!s)|\bcost|early ?bird|presale|per person|fee",
    re.IGNORECASE,
)
# Clause about something other than one entry: punch card, prize, minimum spend, add-on, monthly fee, series pass.
_NOT_A_TICKET = re.compile(
    r"כרטיסי?יה|פרס|מינימום|תוספת|חודשי|סדרה|מנוי|punch|prize|minimum|per month|monthly|add[- ]on|series|subscription",
    re.IGNORECASE,
)
_FREE = re.compile(
    r"ה?כניסה[^.\n|]{0,12}?(?:חופשית|חינם|ללא תשלום)|כניסה חינם"
    r"|free (?:entry|entrance|admission|event)|(?:entry|entrance|admission) is (?:totally |completely )?free"
    r"|free to attend",
    re.IGNORECASE,
)
# A bigger "price" in a guide's text is a prize or a fundraising goal, not a ticket.
MAX_PRICE = Decimal(3000)


def _decimal(value: str) -> Decimal:
    return Decimal(value.replace(",", ""))


def text_prices(text: str | None) -> list[Decimal]:
    """Ticket amounts written in ``text``, lowest first."""
    found: set[Decimal] = set()
    for sentence in _SENTENCE.split(text or ""):
        if not _TICKET_WORDS.search(sentence):
            continue
        start, skipping = 0, False
        for m in _AMOUNT.finditer(sentence):
            comma = sentence.find(",", m.end())
            end = comma if comma != -1 else len(sentence)
            after = _AMOUNT.search(sentence, m.end())
            clause = sentence[start : min(end, after.start() if after else end)]
            # A punch card's amounts run on to the next comma ("160₪ ל-4 כניסות (סטודנטים 140₪)").
            skipping = (skipping and "," not in sentence[start : m.start()]) or bool(_NOT_A_TICKET.search(clause))
            start = m.end()
            if skipping:
                continue
            found.update(_decimal(v) for v in m.groups() if v)
    return sorted(p for p in found if 0 < p <= MAX_PRICE)


def text_tickets(text: str | None) -> list[TicketTier]:
    """Tiers for the prices ``text`` writes out: a free-entry tier and one per amount."""
    tiers = [TicketTier(name="כניסה חופשית", price=Decimal(0), is_free=True)] if _FREE.search(text or "") else []
    return tiers + [TicketTier(name="כרטיס", price=p) for p in text_prices(text)]
