"""Saved events: the signed-in user's hearts, cards for a signed-out browser's hearts, and clean-up."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, defer, selectinload

from partylinks.accounts.guard import SameOrigin, limited
from partylinks.accounts.sessions import UserDep
from partylinks.db import EventRow, upcoming_filter
from partylinks.saved.models import SavedEventRow

MAX_CARDS = 100  # a signed-out browser's hearts per request
NO_STORE = {"Cache-Control": "private, no-store"}
WRITE = [SameOrigin, limited("me", 30)]

router = APIRouter(tags=["saved"], include_in_schema=False)


class MergeIn(BaseModel):
    ids: list[str] = Field(default_factory=list, max_length=500)


def cards(session: Session, ids: list[str]) -> list[dict[str, Any]]:
    """Event cards for these ids (or slugs), as /events shows them: each one's group primary
    (a regrouped event still shows), upcoming only, soonest first, each event once."""
    from partylinks.api import EventSummaryOut, _summary_extras  # local: api imports this module

    if not ids:
        return []
    groups = select(EventRow.group_id).where(or_(EventRow.id.in_(ids), EventRow.slug.in_(ids)))
    rows = session.scalars(
        select(EventRow)
        .options(selectinload(EventRow.images), defer(EventRow.description), defer(EventRow.extra))
        .where(EventRow.group_id.in_(groups), EventRow.is_primary.is_(True), upcoming_filter())
        .order_by(EventRow.start_at, EventRow.id)
    ).all()
    extras = _summary_extras(session, rows)
    return [EventSummaryOut.model_validate(r).model_copy(update=extras[r.id]).model_dump(mode="json") for r in rows]


def _upcoming_ids(session: Session, ids: list[str]) -> list[str]:
    """The ids of these that are known, upcoming events (slugs resolved to ids)."""
    if not ids:
        return []
    return list(session.scalars(
        select(EventRow.id).where(or_(EventRow.id.in_(ids), EventRow.slug.in_(ids)), upcoming_filter())
    ))  # fmt: skip


def _saved_ids(session: Session, user_id: str) -> list[str]:
    return list(session.scalars(select(SavedEventRow.event_id).where(SavedEventRow.user_id == user_id)))


def _out(session: Session, user_id: str) -> dict[str, Any]:
    ids = _saved_ids(session, user_id)
    return {"ids": ids, "items": cards(session, ids)}


@router.get("/api/me/saved")
def list_saved(user: UserDep, request: Request, response: Response) -> dict[str, Any]:
    response.headers.update(NO_STORE)
    with request.app.state.db.session(write=False) as session:
        return _out(session, user.id)


@router.put("/api/me/saved/{event_id}", dependencies=WRITE)
def save(event_id: str, user: UserDep, request: Request, response: Response) -> dict[str, list[str]]:
    response.headers.update(NO_STORE)
    with request.app.state.db.session() as session:
        found = _upcoming_ids(session, [event_id])
        if not found:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such upcoming event")
        have = _saved_ids(session, user.id)
        if found[0] not in have and len(have) >= request.app.state.settings.accounts.max_saved:
            raise HTTPException(status.HTTP_409_CONFLICT, "too many saved events")
        session.execute(insert(SavedEventRow).values(user_id=user.id, event_id=found[0]).on_conflict_do_nothing())
        return {"ids": _saved_ids(session, user.id)}


@router.delete("/api/me/saved/{event_id}", dependencies=WRITE)
def unsave(event_id: str, user: UserDep, request: Request, response: Response) -> dict[str, list[str]]:
    response.headers.update(NO_STORE)
    with request.app.state.db.session() as session:
        ids = [event_id, *session.scalars(select(EventRow.id).where(EventRow.slug == event_id))]
        session.execute(delete(SavedEventRow).where(SavedEventRow.user_id == user.id, SavedEventRow.event_id.in_(ids)))
        return {"ids": _saved_ids(session, user.id)}


@router.post("/api/me/saved/merge", dependencies=WRITE)
def merge(body: MergeIn, user: UserDep, request: Request, response: Response) -> dict[str, Any]:
    """The hearts this browser kept while signed out: known, upcoming ones join the account."""
    response.headers.update(NO_STORE)
    room = request.app.state.settings.accounts.max_saved
    with request.app.state.db.session() as session:
        have = set(_saved_ids(session, user.id))
        new = [i for i in _upcoming_ids(session, body.ids) if i not in have][: max(room - len(have), 0)]
        if new:
            rows = [{"user_id": user.id, "event_id": i} for i in new]
            session.execute(insert(SavedEventRow).values(rows).on_conflict_do_nothing())
        return _out(session, user.id)


@router.get("/events/cards", tags=["events"])
def event_cards(request: Request, ids: Annotated[list[str], Query(max_length=MAX_CARDS)]) -> dict[str, Any]:
    """Cards for a signed-out browser's saved events (ids or slugs; ended ones are left out)."""
    with request.app.state.db.session(write=False) as session:
        return {"items": cards(session, ids[:MAX_CARDS])}


def delete_saved(session: Session, event_ids: list[str]) -> None:
    """Called by ``db.delete_events``: hearts go with their event (never past events)."""
    session.execute(delete(SavedEventRow).where(SavedEventRow.event_id.in_(event_ids)))


def saved_counts(session: Session, user_ids: list[str]) -> dict[str, int]:
    """For the admin's users tab."""
    query = (
        select(SavedEventRow.user_id, func.count())
        .where(SavedEventRow.user_id.in_(user_ids))
        .group_by(SavedEventRow.user_id)
    )
    return {u: n for u, n in session.execute(query)}
