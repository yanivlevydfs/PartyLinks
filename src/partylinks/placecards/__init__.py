"""Place cards: a place (a bar's happy hour, an escape room, a café) is one card, not one per day.

A place is stored as one listing per day it is open (``venues.PLACE_LISTING``, see scrapers/happytlv):
that is what puts it on the right day of a date filter and on /now. Lists show only its first opening
among the rows a filter matched (``first_opening``), so a week of a bar's happy hours is one card on
/genre/happy_hour, the map and the SEO pages, and still the right one on a single day.

``card_extras`` adds what a place card shows beside an event card's fields: the deal ("1+1 על
שתייה") and the price level (₪ to ₪₪₪₪), read from the listing's ``extra`` (which the card query leaves out).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from partylinks.db import EventRow
from partylinks.venues import PLACE_LISTING

IS_PLACE = func.coalesce(EventRow.extra[PLACE_LISTING].as_boolean(), False)
PRICE_LEVEL = EventRow.extra["price_level"].as_integer()  # ₪ to ₪₪₪₪ as 1-4 (scrapers/happytlv.price_level)


def first_opening(where: Sequence[ColumnElement[bool]]) -> ColumnElement[bool]:
    """Events, plus each place's first listing (its URL: one page per place) among those ``where`` matches."""
    first = (
        select(EventRow.id)
        .where(*where, IS_PLACE)
        .ext(distinct_on(EventRow.url))
        .order_by(EventRow.url, EventRow.start_at, EventRow.id)
    )
    return or_(~IS_PLACE, EventRow.id.in_(first))


def card_extras(session: Session, rows: Sequence[Any]) -> dict[str, dict[str, Any]]:
    """{id: {"place": True, "deal": ...}} for the place listings among ``rows``; events get nothing."""
    ids = [r.id for r in rows]
    if not ids:
        return {}
    found = session.execute(
        select(EventRow.id, EventRow.extra["deal"].as_string(), PRICE_LEVEL).where(EventRow.id.in_(ids), IS_PLACE)
    ).all()
    return {i: {"place": True, "deal": deal, "price_level": level} for i, deal, level in found}


__all__ = ["IS_PLACE", "PRICE_LEVEL", "card_extras", "first_opening"]
