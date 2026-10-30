"""A signed-in user's alerts, the bell and the "For you" row; the clean-up hook and the admin's counts."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from cachetools import TTLCache
from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from partylinks.accounts.guard import SameOrigin, limited
from partylinks.accounts.sessions import UserDep
from partylinks.alerts.criteria import Criteria, unknown
from partylinks.alerts.models import AlertHitRow, AlertRow
from partylinks.config import Settings
from partylinks.db import EventRow, upcoming_filter, utcnow
from partylinks.saved.routes import cards

NO_STORE = {"Cache-Control": "private, no-store"}
WRITE = [SameOrigin, limited("me", 30)]
BELL_SIZE = 30
FOR_YOU = 12
PREVIEW = 6
# "For you" per user for 10 minutes; an alert change clears that user's entry.
_for_you: TTLCache[str, list[dict[str, Any]]] = TTLCache(maxsize=10_000, ttl=600)

router = APIRouter(prefix="/api/me", tags=["alerts"], include_in_schema=False)


class AlertIn(BaseModel):
    criteria: Criteria
    name: str = Field("", max_length=80)
    instant: bool = False


class AlertPatch(BaseModel):
    criteria: Criteria | None = None
    name: str | None = Field(None, max_length=80)
    instant: bool | None = None
    active: bool | None = None


def _live() -> Any:
    """A match of an event not over yet: an ended one is neither shown nor counted (and the matcher's pass
    deletes it, before retention gets to the event)."""
    return AlertHitRow.event_id.in_(select(EventRow.id).where(upcoming_filter()))


def _shown() -> Any:
    """A match still in the bell: not over and not cleared by the user."""
    return and_(AlertHitRow.cleared_at.is_(None), _live())


def _today(cfg: Settings) -> Any:
    return datetime.now(ZoneInfo(cfg.timezone)).date()


def where(criteria: Criteria, cfg: Settings) -> list[Any]:
    """The /events WHERE clauses of these criteria (upcoming, one listing per event)."""
    from partylinks.api import build_event_filters  # local: api imports this module

    return build_event_filters(cfg, **criteria.params(_today(cfg)))


def _checked(criteria: Criteria, cfg: Settings) -> Criteria:
    if criteria.is_empty():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "an alert needs at least one filter")
    if bad := unknown(criteria, cfg):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "unknown " + ", ".join(bad))
    return criteria


def _out(a: AlertRow, unseen: int = 0) -> dict[str, Any]:
    return {
        "id": a.id, "name": a.name, "criteria": a.criteria, "instant": a.instant, "active": a.active,
        "created_at": a.created_at, "unseen": unseen,
    }  # fmt: skip


def _mine(session: Session, user_id: str, alert_id: int) -> AlertRow:
    alert = session.get(AlertRow, alert_id)
    if alert is None or alert.user_id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such alert")
    return alert


@router.get("/alerts")
def list_alerts(user: UserDep, request: Request, response: Response) -> dict[str, Any]:
    response.headers.update(NO_STORE)
    with request.app.state.db.session(write=False) as session:
        alerts = session.scalars(select(AlertRow).where(AlertRow.user_id == user.id).order_by(AlertRow.id)).all()
        unseen = dict(
            session.execute(
                select(AlertHitRow.alert_id, func.count())
                .where(AlertHitRow.user_id == user.id, AlertHitRow.seen_at.is_(None), _shown())
                .group_by(AlertHitRow.alert_id)
            ).all()
        )
        return {
            "items": [_out(a, unseen.get(a.id, 0)) for a in alerts],
            "max": request.app.state.settings.accounts.max_alerts,
        }


@router.post("/alerts", status_code=201, dependencies=WRITE)
def create_alert(body: AlertIn, user: UserDep, request: Request, response: Response) -> dict[str, Any]:
    response.headers.update(NO_STORE)
    cfg: Settings = request.app.state.settings
    criteria = _checked(body.criteria, cfg)
    with request.app.state.db.session() as session:
        mine = session.scalars(select(AlertRow).where(AlertRow.user_id == user.id)).all()
        if same := next((a for a in mine if a.criteria_hash == criteria.digest()), None):
            return _out(same)  # the same search saved twice is one alert
        if len(mine) >= cfg.accounts.max_alerts:
            raise HTTPException(status.HTTP_409_CONFLICT, "too many alerts")
        alert = AlertRow(
            user_id=user.id, name=body.name.strip() or criteria.q or "Alert", criteria=criteria.model_dump(),
            criteria_hash=criteria.digest(), instant=body.instant,
        )  # fmt: skip
        session.add(alert)
        session.flush()
        _for_you.pop(user.id, None)
        return _out(alert)


@router.patch("/alerts/{alert_id}", dependencies=WRITE)
def edit_alert(alert_id: int, body: AlertPatch, user: UserDep, request: Request, response: Response) -> dict[str, Any]:
    response.headers.update(NO_STORE)
    cfg: Settings = request.app.state.settings
    with request.app.state.db.session() as session:
        alert = _mine(session, user.id, alert_id)
        if body.criteria is not None:
            criteria = _checked(body.criteria, cfg)
            alert.criteria, alert.criteria_hash = criteria.model_dump(), criteria.digest()
        if body.name is not None and body.name.strip():
            alert.name = body.name.strip()
        if body.instant is not None:
            alert.instant = body.instant
        if body.active is not None:
            alert.active = body.active
        alert.updated_at = utcnow()
        _for_you.pop(user.id, None)
        return _out(alert)


@router.delete("/alerts/{alert_id}", status_code=204, dependencies=WRITE)
def delete_alert(alert_id: int, user: UserDep, request: Request) -> Response:
    with request.app.state.db.session() as session:
        session.delete(_mine(session, user.id, alert_id))
    _for_you.pop(user.id, None)
    return Response(status_code=204)


@router.get("/alerts/{alert_id}/preview")
def preview(alert_id: int, user: UserDep, request: Request, response: Response) -> dict[str, Any]:
    """What this alert finds right now (as /events shows it)."""
    response.headers.update(NO_STORE)
    with request.app.state.db.session(write=False) as session:
        alert = _mine(session, user.id, alert_id)
        return _matching(session, Criteria.model_validate(alert.criteria), request.app.state.settings)


@router.post("/alerts/preview", dependencies=[limited("me", 60)])
def preview_new(body: Criteria, user: UserDep, request: Request, response: Response) -> dict[str, Any]:
    """The count and first events for criteria not saved yet (the alert form's live "N events now")."""
    response.headers.update(NO_STORE)
    with request.app.state.db.session(write=False) as session:
        return _matching(session, body, request.app.state.settings)


def _matching(session: Session, criteria: Criteria, cfg: Settings) -> dict[str, Any]:
    clauses = where(criteria, cfg)
    total = session.scalar(select(func.count()).select_from(EventRow).where(*clauses)) or 0
    ids = list(
        session.scalars(select(EventRow.id).where(*clauses).order_by(EventRow.start_at, EventRow.id).limit(PREVIEW))
    )
    return {"total": total, "items": cards(session, ids)}


@router.get("/bell")
def bell(user: UserDep, request: Request, response: Response) -> dict[str, Any]:
    """The newest matches of the user's alerts, with the alert's name and whether it was seen."""
    response.headers.update(NO_STORE)
    with request.app.state.db.session(write=False) as session:
        unseen = session.scalar(
            select(func.count()).select_from(AlertHitRow)
            .where(AlertHitRow.user_id == user.id, AlertHitRow.seen_at.is_(None), _shown())
        ) or 0  # fmt: skip
        hits = session.execute(
            select(AlertHitRow.event_id, AlertHitRow.matched_at, AlertHitRow.seen_at, AlertRow.name)
            .join(AlertRow, AlertRow.id == AlertHitRow.alert_id)
            .where(AlertHitRow.user_id == user.id, _shown())
            .order_by(AlertHitRow.matched_at.desc())
            .limit(BELL_SIZE * 2)  # one event can match two alerts: shown once
        ).all()
        first: dict[str, Any] = {}
        for h in hits:
            first.setdefault(h.event_id, h)
        by_id = {c["id"]: c for c in cards(session, list(first))}
        items = [
            {"event": by_id[e], "alert": h.name, "matched_at": h.matched_at, "seen": h.seen_at is not None}
            for e, h in first.items()
            if e in by_id
        ][:BELL_SIZE]
        return {"unseen": unseen, "items": items}


@router.post("/bell/seen", status_code=204, dependencies=WRITE)
def bell_seen(user: UserDep, request: Request) -> Response:
    with request.app.state.db.session() as session:
        session.execute(
            update(AlertHitRow)
            .where(AlertHitRow.user_id == user.id, AlertHitRow.seen_at.is_(None))
            .values(seen_at=utcnow())
        )
    return Response(status_code=204)


@router.delete("/bell", status_code=204, dependencies=WRITE)
def bell_remove(user: UserDep, request: Request, event_id: str | None = None) -> Response:
    """Remove one notification (``event_id``) or all of them. The match is stamped ``cleared_at``, not deleted:
    "For you" hides a cleared event too, and the key keeps the matcher from adding it back."""
    with request.app.state.db.session() as session:
        where = [AlertHitRow.user_id == user.id, AlertHitRow.cleared_at.is_(None)]
        if event_id is not None:
            where.append(AlertHitRow.event_id == event_id)
        session.execute(update(AlertHitRow).where(*where).values(cleared_at=utcnow()))
    _for_you.pop(user.id, None)
    return Response(status_code=204)


@router.get("/for-you")
def for_you(user: UserDep, request: Request, response: Response) -> dict[str, Any]:
    """Upcoming events matching any of the user's active alerts, soonest first; not those the user cleared
    from the bell."""
    response.headers.update(NO_STORE)
    if (items := _for_you.get(user.id)) is not None:
        return {"items": items}
    cfg: Settings = request.app.state.settings
    with request.app.state.db.session(write=False) as session:
        raw = session.scalars(
            select(AlertRow.criteria).where(AlertRow.user_id == user.id, AlertRow.active.is_(True))
        ).all()
        if not raw:
            items = []
        else:
            any_alert = or_(*(and_(*where(Criteria.model_validate(c), cfg)) for c in raw))
            cleared = select(AlertHitRow.event_id).where(
                AlertHitRow.user_id == user.id, AlertHitRow.cleared_at.is_not(None)
            )
            ids = session.scalars(
                select(EventRow.id)
                .where(any_alert, EventRow.id.not_in(cleared))
                .order_by(EventRow.start_at, EventRow.id)
                .limit(FOR_YOU)
            )
            items = cards(session, list(ids))
    _for_you[user.id] = items
    return {"items": items}


def alert_counts(session: Session, user_ids: list[str]) -> dict[str, int]:
    """For the admin's users tab."""
    query = select(AlertRow.user_id, func.count()).where(AlertRow.user_id.in_(user_ids)).group_by(AlertRow.user_id)
    return {u: n for u, n in session.execute(query)}
