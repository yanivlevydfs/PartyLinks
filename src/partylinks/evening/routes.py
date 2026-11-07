"""GET /events/{ref}/evening: plan the evening around an event (``evening.plan``), cached like every list."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from partylinks.api import EventSummaryOut, _summary_extras, build_event_filters
from partylinks.config import Settings
from partylinks.db import Database, EventRow
from partylinks.evening.plan import plan

router = APIRouter(tags=["events"])


class StopOut(BaseModel):
    event: EventSummaryOut
    distance_m: int


class StepOut(BaseModel):
    key: str  # evening.steps[].key: drinks, dinner, morning...
    stops: list[StopOut]


class EveningOut(BaseModel):
    steps: list[StepOut] = []
    venue: list[EventSummaryOut] = []  # the same place's other listings this week


def evening_for(session: Session, cfg: Settings, ref: str) -> EveningOut | None:
    row = session.scalar(select(EventRow).where(or_(EventRow.id == ref, EventRow.slug == ref)))
    if row is None:
        return None
    found = plan(session, cfg, row, lambda **kw: build_event_filters(cfg, **kw))
    rows = [r for step in found.steps for r, _ in step.items] + found.venue
    extras = _summary_extras(session, rows, cfg)

    def card(r: EventRow) -> EventSummaryOut:
        return EventSummaryOut.model_validate(r).model_copy(update=extras[r.id])

    return EveningOut(
        steps=[StepOut(key=s.key, stops=[StopOut(event=card(r), distance_m=d) for r, d in s.items]) for s in found.steps],
        venue=[card(r) for r in found.venue],
    )


@router.get("/events/{event_ref}/evening", response_model=EveningOut)
def evening(event_ref: str, request: Request) -> Response:
    cache = request.app.state.response_cache
    db: Database = request.app.state.db
    cfg: Settings = request.app.state.settings

    def body() -> tuple[bytes, int]:
        with db.session(write=False) as session:
            out = evening_for(session, cfg, event_ref)
        return (out.model_dump_json().encode(), 200) if out else (b'{"detail":"event not found"}', 404)

    content, code = cache.get((request.url.path, request.url.query), body)
    if code == status.HTTP_404_NOT_FOUND:
        raise HTTPException(code, "event not found")
    return Response(content, media_type="application/json")
