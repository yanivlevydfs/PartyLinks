"""A 0 RSVP tier beside paid tickets is "Free + paid", never "Free" (emails, SEO pages)."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

from partylinks import seo
from partylinks.mail import render


def test_free_and_paid_is_not_labelled_free() -> None:
    card = {"entry_type": "free_and_paid", "min_price": 0, "has_free_entry": True, "currency": "ILS"}
    assert render._price(card, render.TEXT["en"]) == "Free + paid"
    assert render._price({**card, "entry_type": "free"}, render.TEXT["en"]) == "Free entry"
    row = SimpleNamespace(entry_type="free_and_paid", min_price=Decimal(0), currency="ILS")
    assert seo._price(row, "en") == "Free + paid"  # type: ignore[arg-type]
    assert seo._price(SimpleNamespace(entry_type="paid", min_price=Decimal(80), currency="ILS"), "en") == "From ₪80"  # type: ignore[arg-type]
