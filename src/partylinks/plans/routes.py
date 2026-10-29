"""The plan endpoints, the clean-up hook and the link-preview page (see ``partylinks.plans``)."""

from __future__ import annotations

import secrets
from html import escape
from typing import Annotated, Any, Literal

from fastapi import APIRouter, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from partylinks.accounts.guard import SameOrigin, limited, same_origin
from partylinks.accounts.models import Gender, UserRow
from partylinks.accounts.sessions import OptionalUserDep, UserDep, optional_user
from partylinks.db import EventRow
from partylinks.parsing import tidy_text
from partylinks.plans.models import GROUP_NAME_LENGTH, NAME_LENGTH, PlanEventRow, PlanRow, PlanVoteRow

MAX_VOTES = 60  # a plan is for a group of friends, not a crowd
MAX_OPTIONS = 4  # "which party on Friday?": up to four events side by side
MAX_SEATS = 8
Answer = Literal["yes", "maybe", "no"]
Ride = Literal["drive", "need"]
NO_STORE = {"Cache-Control": "private, no-store"}
WRITE = [SameOrigin, limited("me", 30)]
TEXT = {
    "he": {"title": "מי בא איתי?", "desc": "לחצו ותגידו אם אתם באים", "which": "איזו מסיבה?", "which_desc": "לחצו ובחרו לאן הולכים"},
    "en": {"title": "Who's coming?", "desc": "Tap to say if you're in", "which": "Which party?", "which_desc": "Tap and pick where we go"},
}


class PlanIn(BaseModel):
    event_id: str = Field(min_length=1, max_length=128)  # id or slug
    name: str | None = Field(default=None, max_length=GROUP_NAME_LENGTH)  # the group's name (signed in)


class NameIn(BaseModel):
    name: str = Field(max_length=GROUP_NAME_LENGTH)  # empty clears it


class OptionIn(BaseModel):
    event_id: str = Field(min_length=1, max_length=128)


class VoteIn(BaseModel):
    voter: str = Field(min_length=8, max_length=36, pattern=r"^[A-Za-z0-9-]+$")
    name: str = Field(min_length=1, max_length=NAME_LENGTH)
    answer: Answer
    picks: list[str] | None = Field(default=None, max_length=MAX_OPTIONS)  # option event ids (several events)
    ride: Ride | None = None
    seats: int | None = Field(default=None, ge=1, le=MAX_SEATS)  # with ride=drive
    gender: Gender | None = None  # signed in, also kept on the account


class VoteOut(BaseModel):
    name: str
    answer: Answer
    gender: Gender | None
    mine: bool
    picks: list[str]
    ride: Ride | None
    seats: int | None


class PlanOut(BaseModel):
    id: str
    name: str | None
    owner: bool  # the viewer made it (may rename or delete it)
    in_groups: bool  # it is in the viewer's "my groups" (made it, or answered signed in)
    event: Any  # option one: api.EventSummaryOut (imported late: api includes this router)
    options: list[Any]  # every event of the plan, option one first (one entry in a one-event plan)
    votes: list[VoteOut]


router = APIRouter(tags=["plans"])


def _option_ids(session: Session, plan: PlanRow) -> list[str]:
    extra = session.scalars(select(PlanEventRow.event_id).where(PlanEventRow.plan_id == plan.id).order_by(PlanEventRow.position))
    return [plan.event_id, *extra]


def _summaries(session: Session, events: list[EventRow]) -> list[dict[str, Any]]:
    from partylinks.api import EventSummaryOut, _summary_extras  # local: api imports this module

    extras = _summary_extras(session, events)
    return [EventSummaryOut.model_validate(e).model_copy(update=extras[e.id]).model_dump(mode="json") for e in events]


def _plan_out(session: Session, plan: PlanRow, voter: str | None, user: UserRow | None = None) -> PlanOut:
    ids = _option_ids(session, plan)
    rows = {r.id: r for r in session.scalars(select(EventRow).where(EventRow.id.in_(ids)))}
    events = [rows[i] for i in ids if i in rows]
    if plan.event_id not in rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "plan not found")  # its event is over or gone
    options = _summaries(session, events)
    votes = session.scalars(select(PlanVoteRow).where(PlanVoteRow.plan_id == plan.id).order_by(PlanVoteRow.id)).all()
    uid = user.id if user else None
    owner = uid is not None and plan.owner_id == uid
    return PlanOut(
        id=plan.id,
        name=plan.name,
        owner=owner,
        in_groups=owner or any(uid is not None and v.user_id == uid for v in votes),
        event=options[0],
        options=options,
        votes=[
            VoteOut(
                name=v.name, answer=v.answer, gender=v.gender,  # type: ignore[arg-type]
                mine=(voter is not None and v.voter == voter) or (uid is not None and v.user_id == uid),
                picks=[p for p in v.picks or [] if p in rows], ride=v.ride, seats=v.seats,  # type: ignore[arg-type]
            )
            for v in votes
        ],
    )


def _signed_in(request: Request) -> UserRow | None:
    """The signed-in user behind a plan write from our own pages; a cross-site post stays anonymous
    (it can never put a group in someone's account)."""
    try:
        same_origin(request)
    except HTTPException:
        return None
    return optional_user(request)


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


@router.post("/plans", response_model=PlanOut, status_code=status.HTTP_201_CREATED, dependencies=[limited("plans", 20)])
def create_plan(body: PlanIn, request: Request) -> PlanOut:
    """A new plan for an event; the link to send is ``/plan/<id>``. Made signed in, it is a group in
    the creator's account (``/api/me/groups``) and may carry a name."""
    user = _signed_in(request)
    with request.app.state.db.session() as session:
        plan = PlanRow(id=secrets.token_urlsafe(9), event_id=_event(session, body.event_id).id)
        if user is not None:
            plan.owner_id, plan.name = user.id, tidy_text((body.name or "").replace("\n", " ")) or None
        session.add(plan)
        session.flush()
        return _plan_out(session, plan, None, user)


@router.get("/plans/{plan_id}", response_model=PlanOut)
def get_plan(
    plan_id: str, request: Request, response: Response, user: OptionalUserDep,
    voter: Annotated[str | None, Header(alias="x-plan-voter", max_length=36)] = None,
) -> PlanOut:  # fmt: skip
    """The plan's events and every answer; the browser's voter key (a header, so no log or proxy keeps
    it with the URL) or the signed-in account marks its own."""
    response.headers.update(NO_STORE)  # per viewer: never in a shared cache
    with request.app.state.db.session(write=False) as session:
        return _plan_out(session, _plan(session, plan_id), voter, user)


@router.patch("/plans/{plan_id}", response_model=PlanOut, dependencies=WRITE)
def rename(plan_id: str, body: NameIn, user: UserDep, request: Request) -> PlanOut:
    """The group's name; only its creator."""
    with request.app.state.db.session() as session:
        plan = _plan(session, plan_id)
        if plan.owner_id != user.id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "only the group's creator renames it")
        plan.name = tidy_text(body.name.replace("\n", " ")) or None
        session.flush()
        return _plan_out(session, plan, None, user)


@router.post("/plans/{plan_id}/events", response_model=PlanOut, dependencies=[limited("plans", 20)])
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
        return _plan_out(session, plan, None, _signed_in(request))


@router.post("/plans/{plan_id}/votes", response_model=PlanOut, dependencies=[limited("plan-votes", 60)])
def vote(plan_id: str, body: VoteIn, request: Request) -> PlanOut:
    """Answer, or change this browser's answer (same voter key): in / maybe / no, which of the plan's
    events (when it compares several), and a car with free seats or the need of a ride."""
    name = tidy_text(body.name.replace("\n", " "))
    if not name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "name is empty")
    user = _signed_in(request)
    with request.app.state.db.session() as session:
        plan = _plan(session, plan_id)
        ids = _option_ids(session, plan)
        picks = [p for p in dict.fromkeys(body.picks or []) if p in ids] if len(ids) > 1 and body.answer != "no" else None
        votes = select(PlanVoteRow).where(PlanVoteRow.plan_id == plan_id)
        # Signed in, the account's answer (from any device) comes first; then this browser's.
        row = session.scalar(votes.where(PlanVoteRow.user_id == user.id)) if user else None
        if row is None:
            row = session.scalar(votes.where(PlanVoteRow.voter == body.voter))
            if row is not None and row.user_id is not None and (user is None or row.user_id != user.id):
                # An account's answer changes only signed in as that account, whatever key is sent.
                raise HTTPException(status.HTTP_403_FORBIDDEN, "sign in to change this answer")
        if row is None:
            if (session.scalar(select(func.count()).where(PlanVoteRow.plan_id == plan_id)) or 0) >= MAX_VOTES:
                raise HTTPException(status.HTTP_409_CONFLICT, "this plan is full")
            row = PlanVoteRow(plan_id=plan_id, voter=body.voter)
            session.add(row)
        ride = body.ride if body.answer != "no" else None
        row.name, row.answer, row.picks = name, body.answer, picks
        row.ride, row.seats = ride, (body.seats or 1) if ride == "drive" else None
        row.gender = body.gender or row.gender
        if user is not None:
            row.user_id = user.id
            if body.gender and user.gender != body.gender:  # remembered for the next group
                session.execute(update(UserRow).where(UserRow.id == user.id).values(gender=body.gender))
        session.flush()
        return _plan_out(session, plan, body.voter, user)


class GroupOut(BaseModel):
    id: str
    name: str | None
    owner: bool
    event: Any  # option one: api.EventSummaryOut
    options: int
    answer: Answer | None  # the user's own
    going: int
    maybe: int
    seats: int  # free seats offered
    riders: int
    men: int  # of those in or maybe, by their own answer
    women: int


@router.get("/api/me/groups", response_model=list[GroupOut], include_in_schema=False)
def my_groups(user: UserDep, request: Request, response: Response) -> list[GroupOut]:
    """The user's groups (made or answered signed in), soonest event first; each goes with its event
    (``delete_plans``)."""
    response.headers.update(NO_STORE)
    with request.app.state.db.session(write=False) as session:
        joined = select(PlanVoteRow.plan_id).where(PlanVoteRow.user_id == user.id)
        plans = session.scalars(select(PlanRow).where(or_(PlanRow.owner_id == user.id, PlanRow.id.in_(joined)))).all()
        events = {e.id: e for e in session.scalars(select(EventRow).where(EventRow.id.in_([p.event_id for p in plans])))}
        plans = [p for p in plans if p.event_id in events]
        plans.sort(key=lambda p: (events[p.event_id].start_at is None, events[p.event_id].start_at or 0, p.id))
        cards = _summaries(session, [events[p.event_id] for p in plans])
        ids = [p.id for p in plans]
        votes = session.scalars(select(PlanVoteRow).where(PlanVoteRow.plan_id.in_(ids))).all()
        extra = dict(session.execute(
            select(PlanEventRow.plan_id, func.count()).where(PlanEventRow.plan_id.in_(ids)).group_by(PlanEventRow.plan_id)
        ).all())  # fmt: skip
        out = []
        for p, card in zip(plans, cards, strict=True):
            vs = [v for v in votes if v.plan_id == p.id]
            own = next((v for v in vs if v.user_id == user.id), None)
            out.append(GroupOut(
                id=p.id, name=p.name, owner=p.owner_id == user.id, event=card, options=1 + extra.get(p.id, 0),
                answer=own.answer if own else None,  # type: ignore[arg-type]
                going=sum(v.answer == "yes" for v in vs), maybe=sum(v.answer == "maybe" for v in vs),
                seats=sum(v.seats or 0 for v in vs if v.ride == "drive"), riders=sum(v.ride == "need" for v in vs),
                men=sum(v.gender == "male" and v.answer != "no" for v in vs),
                women=sum(v.gender == "female" and v.answer != "no" for v in vs),
            ))  # fmt: skip
        return out


@router.delete("/api/me/groups/{plan_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=WRITE)
def leave(plan_id: str, user: UserDep, request: Request) -> Response:
    """The creator deletes the group, for everyone; a friend leaves it (their answer goes)."""
    with request.app.state.db.session() as session:
        plan = session.get(PlanRow, plan_id)
        if plan is not None and plan.owner_id == user.id:
            session.execute(delete(PlanVoteRow).where(PlanVoteRow.plan_id == plan_id))
            session.execute(delete(PlanEventRow).where(PlanEventRow.plan_id == plan_id))
            session.delete(plan)
        elif plan is not None:
            session.execute(delete(PlanVoteRow).where(PlanVoteRow.plan_id == plan_id, PlanVoteRow.user_id == user.id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def delete_plans(session: Session, event_ids: list[str]) -> None:
    """Called by ``db.delete_events``: a plan whose own event goes is deleted with its answers and
    options; an event compared in another plan just leaves that plan's options."""
    plans = select(PlanRow.id).where(PlanRow.event_id.in_(event_ids))
    session.execute(delete(PlanVoteRow).where(PlanVoteRow.plan_id.in_(plans)))
    session.execute(delete(PlanEventRow).where(or_(PlanEventRow.plan_id.in_(plans), PlanEventRow.event_id.in_(event_ids))))
    session.execute(delete(PlanRow).where(PlanRow.event_id.in_(event_ids)))


def forget_user(session: Session, user_id: str) -> None:
    """Called by ``accounts.service.delete_user``: the account's answers go; its groups stay for the
    friends, with no creator (no one renames or deletes them, and they go with their event)."""
    session.execute(delete(PlanVoteRow).where(PlanVoteRow.user_id == user_id))
    session.execute(update(PlanRow).where(PlanRow.owner_id == user_id).values(owner_id=None))


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
