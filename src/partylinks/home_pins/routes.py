"""The admin's pins: ``/admin/api/home-pins`` (list, one event's pin and the rows it matches, pin / edit,
unpin, reorder). Events are found with the site's own search (``/events?q=``). Included by partylinks.api."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import exists, func, select

from partylinks.admin import AdminDep
from partylinks.api import HOME_RAILS, SessionDep
from partylinks.config import get_settings
from partylinks.db import EventRow, upcoming_filter
from partylinks.home_pins.models import HomePinRow
from partylinks.home_pins.rails import matches, shown_in

log = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/api/home-pins", tags=["admin"], dependencies=[AdminDep])
# Every row a pin may choose: the fixed ones, the coming holiday's, and the smart ones (smartrails.rail_filters).
ROW_KEYS = {*HOME_RAILS, "holiday", "now", "rainy", "outside", "free_now", "price_down", "new_today", "last_tickets"}


class PinIn(BaseModel):
    rails: list[str] = Field(default_factory=list, max_length=len(ROW_KEYS))  # [] = Trending + every row it matches
    until: datetime | None = None
    note: str | None = Field(None, max_length=500)

    @field_validator("until")
    @classmethod
    def _israel(cls, value: datetime | None) -> datetime | None:
        """The admin's "until 20.11" arrives as Israel's wall clock, not the browser's."""
        return value.replace(tzinfo=ZoneInfo(get_settings().timezone)) if value and value.tzinfo is None else value

    @field_validator("rails")
    @classmethod
    def _known(cls, value: list[str]) -> list[str]:
        if unknown := set(value) - ROW_KEYS:
            raise ValueError(f"unknown rows: {', '.join(sorted(unknown))}")
        return list(dict.fromkeys(value))


def _out(session: Any, p: HomePinRow, e: EventRow | None) -> dict[str, Any]:
    over = p.until is not None and p.until <= datetime.now(p.until.tzinfo)
    return {
        "event_id": p.event_id, "rails": p.rails, "until": p.until, "note": p.note, "position": p.position,
        "title": e and e.title, "slug": e and e.slug, "start_at": e and e.start_at, "venue_name": e and e.venue_name,
        "cover_url": e and (e.cover_url or e.image_url), "appears": [] if over else shown_in(session, p),
    }  # fmt: skip


def _pin_of(session: Any, event_id: str) -> tuple[HomePinRow, EventRow | None] | None:
    return session.execute(
        select(HomePinRow, EventRow).outerjoin(EventRow, EventRow.id == HomePinRow.event_id)
        .where(HomePinRow.event_id == event_id)
    ).first()  # fmt: skip


@router.get("")
def pins(session: SessionDep) -> list[dict[str, Any]]:
    """Every pin, first shown first, with its event and the rows it leads now."""
    found = session.execute(
        select(HomePinRow, EventRow).outerjoin(EventRow, EventRow.id == HomePinRow.event_id)
        .order_by(HomePinRow.position, HomePinRow.created_at)
    ).all()  # fmt: skip
    return [_out(session, p, e) for p, e in found]


@router.get("/event/{event_id:path}")
def event_pin(event_id: str, session: SessionDep) -> dict[str, Any]:
    """One event's pin (None: not pinned) and the rows it matches by itself (✓ in the picker)."""
    found = _pin_of(session, event_id)
    return {
        "pin": found and _out(session, *found),
        "natural": [k for k, ok in matches(session, event_id).items() if ok],
    }


@router.put("/{event_id:path}")
def save(event_id: str, body: PinIn, session: SessionDep) -> dict[str, Any]:
    """Pin an upcoming event (a new pin leads the others) or change its rows, end and note."""
    if not session.scalar(select(exists().where(EventRow.id == event_id, upcoming_filter()))):
        log.info("home pins: refused %s: no such upcoming event", event_id)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{event_id}: no such upcoming event")
    p = session.get(HomePinRow, event_id)
    if p is None:
        first = session.scalar(select(func.min(HomePinRow.position)))
        p = HomePinRow(event_id=event_id, position=(first or 0) - 1)
        session.add(p)
    p.rails, p.until, p.note = body.rails, body.until, body.note
    session.flush()
    log.info("home pins: pinned %s to %s until %s", event_id, body.rails or "auto rows", body.until or "the event ends")
    return _out(session, *(_pin_of(session, event_id) or (p, None)))


@router.delete("/{event_id:path}", status_code=status.HTTP_204_NO_CONTENT)
def unpin(event_id: str, session: SessionDep) -> None:
    """Unpin (already gone: nothing to do)."""
    if p := session.get(HomePinRow, event_id):
        session.delete(p)
        log.info("home pins: unpinned %s", event_id)


@router.post("/order")
def reorder(event_ids: list[str], session: SessionDep) -> dict[str, int]:
    """Every pin's event id, first shown first."""
    moved = 0
    for p in session.scalars(select(HomePinRow).where(HomePinRow.event_id.in_(event_ids))):
        p.position, moved = event_ids.index(p.event_id), moved + 1
    log.info("home pins: reordered %d", moved)
    return {"saved": moved}
