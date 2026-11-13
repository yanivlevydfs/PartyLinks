"""``POST /api/promote-requests`` (the /promote form, signed-in users only, rate-limited), its event picker
``GET /api/promote-requests/events`` and the admin's
``/admin/api/promote-requests`` (list, set status, delete). Included by partylinks.api."""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal

import phonenumbers
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field, StringConstraints
from sqlalchemy import String, and_, cast, or_, select
from sqlalchemy.orm import selectinload

from partylinks.accounts.guard import SameOrigin, limited
from partylinks.accounts.sessions import UserDep
from partylinks.admin import AdminDep
from partylinks.api import SessionDep
from partylinks.config import Settings
from partylinks.db import EventRow, upcoming_filter
from partylinks.promote.models import PromoteRequestRow
from partylinks.search import _pattern

log = logging.getLogger(__name__)
public = APIRouter(tags=["promote"])
admin = APIRouter(prefix="/admin/api/promote-requests", tags=["admin"], dependencies=[AdminDep])

PLACEMENTS = ("rail", "results", "home", "unsure")  # web/src/promote/i18n.ts has their names
STATUSES = ("new", "contacted", "closed")
Line = Annotated[str, StringConstraints(strip_whitespace=True, max_length=160)]


class RequestIn(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)]
    business: Line = ""
    email: EmailStr
    phone: Annotated[str, StringConstraints(strip_whitespace=True, min_length=7, max_length=40)]
    event_ids: list[str] = Field(min_length=1, max_length=50)
    placements: list[Literal["rail", "results", "home", "unsure"]] = Field(default_factory=list, max_length=4)
    message: Annotated[str, StringConstraints(strip_whitespace=True, max_length=10000)] = ""
    lang: Literal["he", "en"] = "he"
    website: str = ""  # hidden from people: a bot that fills it gets a quiet "thanks" and nothing is saved


def _phone(raw: str, region: str) -> str:
    """The mobile number in E.164 (+972525381648), or 422."""
    try:
        number = phonenumbers.parse(raw, region)
    except phonenumbers.NumberParseException:
        number = None
    mobile = (phonenumbers.PhoneNumberType.MOBILE, phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE)
    if number is None or not phonenumbers.is_valid_number(number) or phonenumbers.number_type(number) not in mobile:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "phone: not a valid mobile number")
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


def _out(r: PromoteRequestRow) -> dict[str, Any]:
    return {
        "id": r.id, "name": r.name, "business": r.business, "email": r.email, "phone": r.phone, "events": r.events,
        "placements": r.placements, "message": r.message, "lang": r.lang, "status": r.status, "created_at": r.created_at,
        "user_id": r.user_id,
    }  # fmt: skip


@public.get("/api/promote-requests/events", dependencies=[limited("promote-search", "promote_search")])
def pick(q: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=80)], user: UserDep,
         session: SessionDep) -> list[dict[str, Any]]:  # fmt: skip
    """The form's event picker: upcoming events whose title, venue or lineup holds every typed word, soonest
    first. ~0.13 s on prod (2026-11-13), where the site's full search (/events?q=, ranked, counted) takes 1.6-2 s
    a keystroke. Signed-in users only, as the form."""
    words = q.split()[:5]
    found = session.scalars(
        select(EventRow)
        .options(selectinload(EventRow.images))
        .where(
            upcoming_filter(), EventRow.is_primary.is_(True),
            and_(*(or_(EventRow.title.ilike(_pattern(w), escape="\\"), EventRow.venue_name.ilike(_pattern(w), escape="\\"),
                       cast(EventRow.lineup, String).ilike(_pattern(w), escape="\\")) for w in words)),
        )
        .order_by(EventRow.start_at)
        .limit(8)
    )  # fmt: skip
    return [
        {"id": e.id, "slug": e.slug, "title": e.title, "start_at": e.start_at, "venue_name": e.venue_name,
         "city": e.city, "cover_url": e.cover_url, "image_url": None}
        for e in found
    ]  # fmt: skip


@public.post(
    "/api/promote-requests",
    status_code=status.HTTP_201_CREATED,
    dependencies=[limited("promote", "promote"), SameOrigin],
)
def create(
    body: RequestIn, request: Request, user: UserDep, session: SessionDep
) -> dict[str, Any]:
    """Save a signed-in user's request (signed out: 401) for upcoming events only: any id that is not an
    upcoming event answers 422. The owner asked for sign-in (2026-11-13): a request comes from a known account."""
    cfg: Settings = request.app.state.settings
    if not cfg.promote.enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "promotion requests are closed")
    if body.website:
        log.info("promote request dropped: the hidden field was filled")
        return {"id": 0}
    ids = list(dict.fromkeys(body.event_ids))
    if len(ids) > cfg.promote.max_events:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"event_ids: at most {cfg.promote.max_events}")
    if len(body.message) > cfg.promote.message_max:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"message: at most {cfg.promote.message_max} characters"
        )
    phone = _phone(body.phone, cfg.promote.phone_region)
    rows = {e.id: e for e in session.scalars(select(EventRow).where(EventRow.id.in_(ids), upcoming_filter()))}
    if gone := [i for i in ids if i not in rows]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"event_ids: not upcoming: {', '.join(gone)}")
    events = [
        {"id": e.id, "slug": e.slug, "title": e.title, "venue_name": e.venue_name,
         "start_at": e.start_at.isoformat() if e.start_at else None}
        for e in (rows[i] for i in ids)
    ]  # fmt: skip
    r = PromoteRequestRow(
        name=body.name, business=body.business or None, email=body.email, phone=phone, events=events,
        placements=list(dict.fromkeys(body.placements)), message=body.message or None, lang=body.lang, user_id=user.id,
    )  # fmt: skip
    session.add(r)
    session.flush()
    log.info(
        "promote request %s from user %s: %d events, placements %s", r.id, user.id, len(events), r.placements or "-"
    )  # no admin email (the owner, 2026-11-13: save on sending); the admin's requests tab lists it
    return {"id": r.id}


@admin.get("")
def requests(request: Request, session: SessionDep) -> list[dict[str, Any]]:
    """The latest requests, newest first."""
    shown = request.app.state.settings.promote.admin_shown
    found = session.scalars(select(PromoteRequestRow).order_by(PromoteRequestRow.id.desc()).limit(shown))
    return [_out(r) for r in found]


class StatusIn(BaseModel):
    status: Literal["new", "contacted", "closed"]


@admin.patch("/{request_id}")
def set_status(request_id: int, body: StatusIn, session: SessionDep) -> dict[str, Any]:
    r = session.get(PromoteRequestRow, request_id)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such request")
    r.status = body.status
    log.info("promote request %s: %s", request_id, body.status)
    return _out(r)


@admin.delete("/{request_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove(request_id: int, session: SessionDep) -> None:
    """Delete a request and the person's details in it (already gone: nothing to do)."""
    if r := session.get(PromoteRequestRow, request_id):
        session.delete(r)
        log.info("promote request %s: deleted", request_id)
