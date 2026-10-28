"""The plan endpoints, the clean-up hook and the link-preview page (see ``partylinks.plans``)."""

from __future__ import annotations

import secrets
from html import escape
from typing import Any, Literal

from fastapi import APIRouter, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from partylinks.db import EventRow
from partylinks.parsing import tidy_text
from partylinks.plans.models import NAME_LENGTH, PlanEventRow, PlanRow, PlanVoteRow

MAX_VOTES = 60  # a plan is for a group of friends, not a crowd
MAX_OPTIONS = 4  # "which party on Friday?": up to four events side by side
MAX_SEATS = 8
Answer = Literal["yes", "maybe", "no"]
Ride = Literal["drive", "need"]
TEXT = {
    "he": {"title": "מי בא איתי?", "desc": "לחצו ותגידו אם אתם באים", "which": "איזו מסיבה?", "which_desc": "לחצו ובחרו לאן הולכים"},
    "en": {"title": "Who's coming?", "desc": "Tap to say if you're in", "which": "Which party?", "which_desc": "Tap and pick where we go"},
}


class PlanIn(BaseModel):
    event_id: str = Field(min_length=1, max_length=128)  # id or slug


class OptionIn(BaseModel):
    event_id: str = Field(min_length=1, max_length=128)


class VoteIn(BaseModel):
    voter: str = Field(min_length=8, max_length=36, pattern=r"^[A-Za-z0-9-]+$")
    name: str = Field(min_length=1, max_length=NAME_LENGTH)
    answer: Answer
    picks: list[str] | None = Field(default=None, max_length=MAX_OPTIONS)  # option event ids (several events)
    ride: Ride | None = None
    seats: int | None = Field(default=None, ge=1, le=MAX_SEATS)  # with ride=drive


class VoteOut(BaseModel):
    name: str
    answer: Answer
    mine: bool
    picks: list[str]
    ride: Ride | None
    seats: int | None


class PlanOut(BaseModel):
    id: str
    event: Any  # option one: api.EventSummaryOut (imported late: api includes this router)
    options: list[Any]  # every event of the plan, option one first (one entry in a one-event plan)
    votes: list[VoteOut]


router = APIRouter(tags=["plans"])


def _option_ids(session: Session, plan: PlanRow) -> list[str]:
    extra = session.scalars(select(PlanEventRow.event_id).where(PlanEventRow.plan_id == plan.id).order_by(PlanEventRow.position))
    return [plan.event_id, *extra]


def _plan_out(session: Session, plan: PlanRow, voter: str | None) -> PlanOut:
    from partylinks.api import EventSummaryOut, _summary_extras  # local: api imports this module

    ids = _option_ids(session, plan)
    rows = {r.id: r for r in session.scalars(select(EventRow).where(EventRow.id.in_(ids)))}
    events = [rows[i] for i in ids if i in rows]
    if plan.event_id not in rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "plan not found")  # its event is over or gone
    extras = _summary_extras(session, events)
    options = [EventSummaryOut.model_validate(e).model_copy(update=extras[e.id]).model_dump(mode="json") for e in events]
    votes = session.scalars(select(PlanVoteRow).where(PlanVoteRow.plan_id == plan.id).order_by(PlanVoteRow.id)).all()
    return PlanOut(
        id=plan.id,
        event=options[0],
        options=options,
        votes=[
            VoteOut(
                name=v.name, answer=v.answer, mine=voter is not None and v.voter == voter,  # type: ignore[arg-type]
                picks=[p for p in v.picks or [] if p in rows], ride=v.ride, seats=v.seats,  # type: ignore[arg-type]
            )
            for v in votes
        ],
    )


def _plan(session: Session, plan_id: str) -> PlanRow:
    plan = session.get(PlanRow, plan_id)
    if plan is None or session.get(EventRow, plan.event_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "plan not found")
    return plan


def _event(session: Session, ref: str) -> EventRow:
    event = session.scalar(select(EventRow).where(or_(EventRow.id == ref, EventRow.slug == ref)))
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found")
    return event


@router.post("/plans", response_model=PlanOut, status_code=status.HTTP_201_CREATED)
def create_plan(body: PlanIn, request: Request) -> PlanOut:
    """A new plan for an event; the link to send is ``/plan/<id>``."""
    with request.app.state.db.session() as session:
        plan = PlanRow(id=secrets.token_urlsafe(6), event_id=_event(session, body.event_id).id)
        session.add(plan)
        session.flush()
        return _plan_out(session, plan, None)


@router.get("/plans/{plan_id}", response_model=PlanOut)
def get_plan(plan_id: str, request: Request, me: str | None = None) -> PlanOut:
    """The plan's events and every answer; ``me`` (the browser's voter key) marks its own."""
    with request.app.state.db.session(write=False) as session:
        return _plan_out(session, _plan(session, plan_id), me)


@router.post("/plans/{plan_id}/events", response_model=PlanOut)
def add_option(plan_id: str, body: OptionIn, request: Request) -> PlanOut:
    """Another event to compare ("which party on Friday?"), up to ``MAX_OPTIONS``; anyone with the link."""
    with request.app.state.db.session() as session:
        plan = _plan(session, plan_id)
        event, ids = _event(session, body.event_id), _option_ids(session, plan)
        if event.id not in ids:
            if len(ids) >= MAX_OPTIONS:
                raise HTTPException(status.HTTP_409_CONFLICT, "this plan has the most events it can compare")
            session.add(PlanEventRow(plan_id=plan.id, event_id=event.id, position=len(ids)))
            session.flush()
        return _plan_out(session, plan, None)


@router.post("/plans/{plan_id}/votes", response_model=PlanOut)
def vote(plan_id: str, body: VoteIn, request: Request) -> PlanOut:
    """Answer, or change this browser's answer (same voter key): in / maybe / no, which of the plan's
    events (when it compares several), and a car with free seats or the need of a ride."""
    name = tidy_text(body.name.replace("\n", " "))
    if not name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "name is empty")
    with request.app.state.db.session() as session:
        plan = _plan(session, plan_id)
        ids = _option_ids(session, plan)
        picks = [p for p in dict.fromkeys(body.picks or []) if p in ids] if len(ids) > 1 and body.answer != "no" else None
        row = session.scalar(select(PlanVoteRow).where(PlanVoteRow.plan_id == plan_id, PlanVoteRow.voter == body.voter))
        if row is None:
            if (session.scalar(select(func.count()).where(PlanVoteRow.plan_id == plan_id)) or 0) >= MAX_VOTES:
                raise HTTPException(status.HTTP_409_CONFLICT, "this plan is full")
            row = PlanVoteRow(plan_id=plan_id, voter=body.voter)
            session.add(row)
        ride = body.ride if body.answer != "no" else None
        row.name, row.answer, row.picks = name, body.answer, picks
        row.ride, row.seats = ride, (body.seats or 1) if ride == "drive" else None
        session.flush()
        return _plan_out(session, plan, body.voter)


def delete_plans(session: Session, event_ids: list[str]) -> None:
    """Called by ``db.delete_events``: a plan whose own event goes is deleted with its answers and
    options; an event compared in another plan just leaves that plan's options."""
    plans = select(PlanRow.id).where(PlanRow.event_id.in_(event_ids))
    session.execute(delete(PlanVoteRow).where(PlanVoteRow.plan_id.in_(plans)))
    session.execute(delete(PlanEventRow).where(or_(PlanEventRow.plan_id.in_(plans), PlanEventRow.event_id.in_(event_ids))))
    session.execute(delete(PlanRow).where(PlanRow.event_id.in_(event_ids)))


def register_page(app: FastAPI, page: Any) -> None:
    """``/plan/<id>`` rendered on the server, for link previews (WhatsApp reads the title and the
    poster); the web app then shows the poll. ``page`` is ``seo.register``'s cached renderer."""
    from partylinks import seo  # local: seo calls this from its register()

    def plan_page(session: Session, cfg: Any, lang: str, plan_id: str) -> Any:
        plan = session.get(PlanRow, plan_id)
        row = session.get(EventRow, plan.event_id) if plan else None
        if plan is None or row is None:
            return None
        t, when = TEXT[lang], seo._when(row, cfg, lang)
        others = session.scalars(
            select(EventRow.title).join(PlanEventRow, PlanEventRow.event_id == EventRow.id)
            .where(PlanEventRow.plan_id == plan_id).order_by(PlanEventRow.position)
        ).all()  # fmt: skip
        if others:  # "which party?": every option in the preview
            titles = [row.title, *others]
            title, desc = f"{t['which']} {' / '.join(titles)}", f"{when} · {t['which_desc']}"
        else:
            title, desc = f"{t['title']} {row.title}", " · ".join(x for x in (when, row.venue_name or row.city, t["desc"]) if x)
        return seo.Page(
            title=seo._clip(title, 110), description=desc, path=f"/plan/{plan_id}", lang=lang,
            image=seo._cover(cfg, row), noindex=True,
            body=f"<h1>{escape(title)}</h1><p>{escape(when)}</p>",
        )  # fmt: skip

    @app.get("/plan/{plan_id}", include_in_schema=False)
    def plan(plan_id: str, request: Request) -> HTMLResponse:
        return page(request, lambda s, c, lang: plan_page(s, c, lang, plan_id))  # type: ignore[no-any-return]
