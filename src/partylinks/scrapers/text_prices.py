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
_CUR = r"₪|ש\"ח|ש״ח|ש''ח|שח(?![א-ת])|שקלים|שקל(?![א-ת])|\bNIS\b|\bILS\b|shekels?\b"
# "40 ₪", "70-120 ש"ח", "₪70", "NIS 70"
_AMOUNT = re.compile(rf"(?:(?:₪|\bNIS)\s*({_NUM}))|(?:({_NUM})(?:\s*[-–]\s*({_NUM}))?\s*(?:{_CUR}))", re.IGNORECASE)
_SENTENCE = re.compile(r"\n|\||[!?]|\.(?!\d)")
_TICKET_WORDS = re.compile(
    r"כרטיס|כניס|תשלום|משלם|מחיר(?!ים)|עלות|השתתפות|הרשמה|למשתתף|לאורח|דמי|מוקדמ"
    r"|ticket|entry|entrance|admission|price(?!s)|\bcost|early ?bird|presale|per person|fee",
    re.IGNORECASE,
)
# Clause about something other than one entry: punch card, prize, minimum spend, add-on, monthly fee, series pass.
_NOT_A_TICKET = re.compile(
    r"כרטיסי?יה|פרס|לקבוצה|מינימום|תוספת|חודשי|סדרה|מנוי|punch|prize|minimum|per month|monthly|add[- ]on|series|subscription",
    re.IGNORECASE,
)
_FREE = re.compile(
    # "הכניסה (אליה / למתחם) חופשית"; not "כרטיס הכניסה כולל טעימה חופשית" or "פעילות חופשית" (free play).
    r"(?<!כרטיס )(?<!כרטיס ה)ה?(?:כניסה|השתתפות)(?![א-ת])[^.\n|]{0,12}?(?:חופשית|חינם|ללא (?:כל )?(?:עלות|תשלום)(?! נוסף))"
    # One event, not a list of perks ("הצגות, סיורים ללא תשלום, הטבות בעסקים" on a resident card's ad).
    r"|ה?(?:פעילות|אירוע|הרשמה|מופע|הופעה|סיור|הרצאה)(?![א-ת])[^.\n|]{0,12}?(?:חינם|ללא (?:כל )?(?:עלות|תשלום)(?! נוסף))"
    r"|כניסה חינם"
    r"|free (?:entry|entrance|admission|event|of charge|rsvp)"
    r"|(?:entry|entrance|admission|event) is (?:totally |completely |100% )?free|free to attend",
    re.IGNORECASE,
)
# "ללא עלות" / "חינם" alone count too, but not for parking, delivery, cancelling, drinks or one group
# ("חינם לילדים", "חינם למנויי מטמון", "חינם עבור חברי המועדון"); "ללא תשלום נוסף" comes on top of an entry fee.
_BARE_FREE = re.compile(
    r"(?<![א-ת])(?:ללא (?:כל )?(?:עלות|תשלום)(?! נוסף)(?!\s*(?:ל(?:תושב|מנוי|חבר)|עבור))|ב?חינם(?![א-ת])(?!\s*(?:ל|עבור)))"
)
# What a bare "ללא עלות" / "חינם" right after is about, when it is not the entry: parking, delivery, a drink,
# a list of perks ("סיורים ללא תשלום, הטבות"), a service ("טיפולים", "ייעוץ").
_NOT_ENTRY = re.compile(r"חני|משלוח|ביטול|שתי|משקה|כיבוד|מתנה|ספר|ניסיון|ים,? $|טיפול|ייעוץ")
# Who a "free" is for when only part of the audience goes free: kids, residents, soldiers, seniors...
_GROUP = (
    r"(?:ה?ילדים|ה?ילד(?:ות)?|פעוטות|תינוקות|ה?תושבי\S*|ה?חיילי\S*|ה?חיילים|ה?סטודנטים|אזרחים ותיקים|ה?גמלאים"
    r"|ה?מנויי?\S*|חברי\s\S+|ה?נכים|ה?מלווים|ה?מלווה|נשים|kids|children|residents|members|seniors|students|soldiers)"
)
_FREE_FOR_SOME = re.compile(
    rf"(?<![א-ת]){_GROUP}(?![א-ת])[^.\n|]{{0,25}}?(?:נכנסים |נכנס |נכנסות )?(?:ב?חינם|ללא (?:כל )?(?:עלות|תשלום)(?! נוסף)|free)"
    rf"|(?:ב?חינם|ללא (?:כל )?(?:עלות|תשלום)|חופשית|free)\s+(?:ל|עבור\s|for\s){_GROUP}",
    re.IGNORECASE,
)


def free_for_some(text: str | None) -> str | None:
    """The phrase when only part of the audience goes free ("ילדים עד גיל 12 נכנסים ללא תשלום", "חינם לתושבי
    העיר", "הכניסה חופשית לחיילים"); None otherwise. The event is then free and paid, never all free."""
    m = _FREE_FOR_SOME.search(text or "")
    return m.group(0).strip(" ,.;:()–-") if m else None


def says_free(text: str | None) -> bool:
    """True when the text says taking part costs nothing ("הכניסה חופשית", "ללא כל עלות", "free entry"), for
    everyone: a free for one group only is ``free_for_some``."""
    text = text or ""
    if free_for_some(text):
        return False
    if _FREE.search(text):
        return True
    return any(not _NOT_ENTRY.search(text[max(0, m.start() - 25) : m.start()]) for m in _BARE_FREE.finditer(text))


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
    tiers = [TicketTier(name="כניסה חופשית", price=Decimal(0), is_free=True)] if says_free(text) else []
    return tiers + [TicketTier(name="כרטיס", price=p) for p in text_prices(text)]
