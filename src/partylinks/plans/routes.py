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
from partylinks.plans.models import NAME_LENGTH, PlanRow, PlanVoteRow

MAX_VOTES = 60  # a plan is for a group of friends, not a crowd
Answer = Literal["yes", "maybe", "no"]
TEXT = {
    "he": {"title": "מי בא איתי?", "desc": "לחצו ותגידו אם אתם באים"},
    "en": {"title": "Who's coming?", "desc": "Tap to say if you're in"},
}


class PlanIn(BaseModel):
    event_id: str = Field(min_length=1, max_length=128)  # id or slug


class VoteIn(BaseModel):
    voter: str = Field(min_length=8, max_length=36, pattern=r"^[A-Za-z0-9-]+$")
    name: str = Field(min_length=1, max_length=NAME_LENGTH)
    answer: Answer


class VoteOut(BaseModel):
    name: str
    answer: Answer
    mine: bool


class PlanOut(BaseModel):
    id: str
    event: Any  # api.EventSummaryOut (imported late: api includes this router)
    votes: list[VoteOut]


router = APIRouter(tags=["plans"])


def _plan_out(session: Session, plan: PlanRow, voter: str | None) -> PlanOut:
    from partylinks.api import EventSummaryOut, _summary_extras  # local: api imports this module

    event = session.get(EventRow, plan.event_id)
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "plan not found")  # its event is over or gone
    votes = session.scalars(select(PlanVoteRow).where(PlanVoteRow.plan_id == plan.id).order_by(PlanVoteRow.id)).all()
    summary = EventSummaryOut.model_validate(event).model_copy(update=_summary_extras(session, [event])[event.id])
    return PlanOut(
        id=plan.id,
        event=summary.model_dump(mode="json"),
        votes=[VoteOut(name=v.name, answer=v.answer, mine=voter is not None and v.voter == voter) for v in votes],  # type: ignore[arg-type]
    )


@router.post("/plans", response_model=PlanOut, status_code=status.HTTP_201_CREATED)
def create_plan(body: PlanIn, request: Request) -> PlanOut:
    """A new plan for an event; the link to send is ``/plan/<id>``."""
    with request.app.state.db.session() as session:
        event = session.scalar(select(EventRow).where(or_(EventRow.id == body.event_id, EventRow.slug == body.event_id)))
        if event is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found")
        plan = PlanRow(id=secrets.token_urlsafe(6), event_id=event.id)
        session.add(plan)
        session.flush()
        return _plan_out(session, plan, None)


@router.get("/plans/{plan_id}", response_model=PlanOut)
def get_plan(plan_id: str, request: Request, me: str | None = None) -> PlanOut:
    """The plan's event and every answer; ``me`` (the browser's voter key) marks its own."""
    with request.app.state.db.session(write=False) as session:
        plan = session.get(PlanRow, plan_id)
        if plan is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "plan not found")
        return _plan_out(session, plan, me)


@router.post("/plans/{plan_id}/votes", response_model=PlanOut)
def vote(plan_id: str, body: VoteIn, request: Request) -> PlanOut:
    """Answer, or change this browser's answer (same voter key)."""
    name = tidy_text(body.name.replace("\n", " "))
    if not name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "name is empty")
    with request.app.state.db.session() as session:
        plan = session.get(PlanRow, plan_id)
        if plan is None or session.get(EventRow, plan.event_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "plan not found")
        row = session.scalar(select(PlanVoteRow).where(PlanVoteRow.plan_id == plan_id, PlanVoteRow.voter == body.voter))
        if row is None:
            if (session.scalar(select(func.count()).where(PlanVoteRow.plan_id == plan_id)) or 0) >= MAX_VOTES:
                raise HTTPException(status.HTTP_409_CONFLICT, "this plan is full")
            row = PlanVoteRow(plan_id=plan_id, voter=body.voter)
            session.add(row)
        row.name, row.answer = name, body.answer
        session.flush()
        return _plan_out(session, plan, body.voter)


def delete_plans(session: Session, event_ids: list[str]) -> None:
    """The plans (and answers) of events being deleted: called by ``db.delete_events``."""
    plans = select(PlanRow.id).where(PlanRow.event_id.in_(event_ids))
    session.execute(delete(PlanVoteRow).where(PlanVoteRow.plan_id.in_(plans)))
    session.execute(delete(PlanRow).where(PlanRow.event_id.in_(event_ids)))


def register_page(app: FastAPI, page: Any) -> None:
    """``/plan/<id>`` rendered on the server, for link previews (WhatsApp reads the title and the
    poster); the web app then shows the poll. ``page`` is ``seo.register``'s cached renderer."""
    from partylinks import seo  # local: seo calls this from its register()

    def plan_page(session: Session, cfg: Any, lang: str, plan_id: str) -> Any:
        plan = session.get(PlanRow, plan_id)
        row = session.get(EventRow, plan.event_id) if plan else None
        if row is None:
            return None
        t, when = TEXT[lang], seo._when(row, cfg, lang)
        return seo.Page(
            title=f"{t['title']} {row.title}",
            description=" · ".join(x for x in (when, row.venue_name or row.city, t["desc"]) if x),
            path=f"/plan/{plan_id}",
            lang=lang,
            image=seo._cover(cfg, row),
            noindex=True,
            body=f"<h1>{escape(t['title'])} {escape(row.title)}</h1><p>{escape(when)}</p>",
        )

    @app.get("/plan/{plan_id}", include_in_schema=False)
    def plan(plan_id: str, request: Request) -> HTMLResponse:
        return page(request, lambda s, c, lang: plan_page(s, c, lang, plan_id))  # type: ignore[no-any-return]
