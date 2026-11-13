"""The ad endpoints: the side rails (``/events/sponsored``), view / click counting
(``/events/sponsored/hit``) and the admin's picks, switch and report (``/admin/api/sponsored...``).
Included by ``partylinks.api`` (before ``/events/{event_ref}``)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import defer, selectinload

from partylinks.admin import AdminDep
from partylinks.ads import paid
from partylinks.ads.models import SponsoredRow, SponsoredStatRow
from partylinks.api import EventSummaryOut, SessionDep, _summary_extras
from partylinks.config import get_settings
from partylinks.db import EventRow, FlagRow, flag_on, upcoming_filter

router = APIRouter()
admin_router = APIRouter(prefix="/admin/api", tags=["admin"], dependencies=[AdminDep])


class SponsoredOut(BaseModel):
    """The home page's two paid rails, each in the admin's order, and each rail's seconds per spotlight
    (settings ads.rotate_*)."""

    right: list[EventSummaryOut] = []
    left: list[EventSummaryOut] = []
    rotate: dict[str, float] = {}


class SponsoredHitIn(BaseModel):
    event_id: str = Field(max_length=128)
    kind: Literal["view", "click"]
    where: Literal["rails", "results"] = "rails"


@router.get("/events/sponsored", response_model=SponsoredOut, tags=["events"])
def sponsored(session: SessionDep) -> SponsoredOut:
    """Paid placements (picked in the admin) that are still upcoming and within their campaign;
    none at all while the admin's sponsored switch is off. Not in the response cache: an admin
    change shows on the next request (the browser keeps its own copy)."""
    ads = get_settings().ads
    rotate = {"right": ads.rotate_right_seconds, "left": ads.rotate_left_seconds}
    if not flag_on(session, "sponsored"):
        return SponsoredOut(rotate=rotate)
    pairs = session.execute(
        select(SponsoredRow.side, EventRow)
        .join(EventRow, EventRow.id == SponsoredRow.event_id)
        .where(upcoming_filter(), or_(SponsoredRow.until.is_(None), SponsoredRow.until > datetime.now(UTC)))
        .where(SponsoredRow.placement.in_(("rails", "both")))
        .order_by(SponsoredRow.position)
        .options(selectinload(EventRow.images), defer(EventRow.description), defer(EventRow.extra))
    ).all()
    extras = _summary_extras(session, [e for _, e in pairs])
    out = SponsoredOut(rotate=rotate)
    for side, e in pairs:
        getattr(out, side).append(EventSummaryOut.model_validate(e).model_copy(update=extras[e.id]))
    return out


@router.post("/events/sponsored/hit", status_code=status.HTTP_204_NO_CONTENT, include_in_schema=False)
def sponsored_hit(body: SponsoredHitIn, request: Request, session: SessionDep) -> Response:
    """One view or click of a sponsored placement (sent by the rails and the results' paid cards).
    Only placements on show count."""
    event = session.get(EventRow, body.event_id)
    if event and session.get(SponsoredRow, body.event_id) and flag_on(session, "sponsored"):
        day = datetime.now(ZoneInfo(request.app.state.settings.timezone)).date()
        # One atomic upsert: the old read-then-write let two first hits of a day both insert, and the
        # second's primary-key clash was a 500 to the visitor.
        view, click = int(body.kind == "view"), int(body.kind == "click")
        results = body.where == "results"
        stat = SponsoredStatRow.__table__.c
        row = {"views": view, "clicks": click, "results_views": view * results, "results_clicks": click * results}
        session.execute(
            pg_insert(SponsoredStatRow)
            .values(event_id=body.event_id, day=day, title=event.title, **row)
            .on_conflict_do_update(
                index_elements=[stat.event_id, stat.day],
                set_={k: func.coalesce(stat[k], 0) + v for k, v in row.items()},
            )
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class SponsoredOneIn(BaseModel):
    """One event's placement, set from the site itself (the event page's 💰, a rail's ✎)."""

    side: Literal["right", "left"]
    until: datetime | None = None  # campaign end; None = while the event is on
    placement: Literal["rails", "results", "both"] = "rails"  # side rails, first in matching results, or both

    @field_validator("until")
    @classmethod
    def _israel(cls, value: datetime | None) -> datetime | None:
        """The admin's "ends 5.11" arrives as Israel's wall clock (2026-11-05T23:59), not the browser's."""
        return value.replace(tzinfo=ZoneInfo(get_settings().timezone)) if value and value.tzinfo is None else value


class SponsoredIn(SponsoredOneIn):
    event_id: str


def _pick_out(s: SponsoredRow, e: EventRow | None, live: bool) -> dict[str, Any]:
    return {
        "event_id": s.event_id,
        "side": s.side,
        "until": s.until,
        "placement": s.placement,
        "title": e.title if e else None,
        "slug": e.slug if e else None,
        "start_at": e.start_at if e else None,
        "venue_name": e.venue_name if e else None,
        "cover_url": (e.cover_url or e.image_url) if e else None,
        "live": live,
    }


@admin_router.get("/sponsored")
def sponsored_list(session: SessionDep) -> dict[str, Any]:
    """The site-wide switch, and every placement in rail order with enough of its event to recognise it
    and whether it is showing (the switch aside)."""
    pairs = session.execute(
        select(SponsoredRow, EventRow)
        .outerjoin(EventRow, EventRow.id == SponsoredRow.event_id)
        .order_by(SponsoredRow.side, SponsoredRow.position)
    ).all()
    ids = [s.event_id for s, _ in pairs]
    upcoming = set(session.scalars(select(EventRow.id).where(EventRow.id.in_(ids), upcoming_filter())))
    now = datetime.now(UTC)
    items = [_pick_out(s, e, s.event_id in upcoming and (s.until is None or s.until > now)) for s, e in pairs]
    return {"enabled": flag_on(session, "sponsored"), "items": items}


@admin_router.get("/sponsored/event/{event_id:path}")
def sponsored_one(event_id: str, session: SessionDep) -> dict[str, Any]:
    """One event's placement (None if it has none) and the site-wide switch: the event page's 💰 sheet."""
    s = session.get(SponsoredRow, event_id)
    e = session.get(EventRow, event_id)
    live = bool(
        s and e and session.scalar(select(EventRow.id).where(EventRow.id == event_id, upcoming_filter()))
        and (s.until is None or s.until > datetime.now(UTC))
    )
    return {"enabled": flag_on(session, "sponsored"), "pick": _pick_out(s, e, live) if s else None}


@admin_router.put("/sponsored/event/{event_id:path}")
def sponsored_one_save(event_id: str, body: SponsoredOneIn, session: SessionDep) -> dict[str, Any]:
    """Sponsor one event, or change its placement. New on a side (or moved to the other one): last there."""
    if session.get(EventRow, event_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found")
    row = session.get(SponsoredRow, event_id)
    if row is None and (session.scalar(select(func.count()).select_from(SponsoredRow)) or 0) >= (
        most := get_settings().api.max_sponsored
    ):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"at most {most} sponsored events")
    if row is None or row.side != body.side:
        last = session.scalar(select(func.max(SponsoredRow.position)).where(SponsoredRow.side == body.side))
        position = -1 if last is None else last
        if row is None:
            row = SponsoredRow(event_id=event_id)
            session.add(row)
        row.side, row.position = body.side, position + 1
    row.until, row.placement = body.until, body.placement
    session.flush()
    paid.sync(session)  # events.is_sponsored
    return sponsored_one(event_id, session)


@admin_router.delete("/sponsored/event/{event_id:path}", status_code=status.HTTP_204_NO_CONTENT)
def sponsored_one_remove(event_id: str, session: SessionDep) -> Response:
    """Stop sponsoring one event (its views and clicks stay in the report)."""
    session.execute(delete(SponsoredRow).where(SponsoredRow.event_id == event_id))
    paid.sync(session)  # events.is_sponsored
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@admin_router.get("/sponsored/stats")
def sponsored_stats(request: Request, session: SessionDep, days: int = Query(30, ge=1, le=3650)) -> dict[str, Any]:
    """Views and clicks per sponsored event over the last ``days`` days (today included), for advertisers."""
    today = datetime.now(ZoneInfo(request.app.state.settings.timezone)).date()
    since = today - timedelta(days=days - 1)
    rows = session.execute(
        select(
            SponsoredStatRow.event_id, func.max(SponsoredStatRow.title).label("title"),
            func.sum(SponsoredStatRow.views).label("views"), func.sum(SponsoredStatRow.clicks).label("clicks"),
            func.min(SponsoredStatRow.day).label("first_day"), func.max(SponsoredStatRow.day).label("last_day"),
            func.coalesce(func.sum(SponsoredStatRow.results_views), 0).label("results_views"),
            func.coalesce(func.sum(SponsoredStatRow.results_clicks), 0).label("results_clicks"),
        )
        .where(SponsoredStatRow.day >= since)
        .group_by(SponsoredStatRow.event_id)
        .order_by(func.sum(SponsoredStatRow.clicks).desc(), func.sum(SponsoredStatRow.views).desc())
    ).all()  # fmt: skip
    events = [dict(r._mapping) for r in rows]
    return {
        "since": since,
        "until": today,
        "views": sum(e["views"] for e in events),
        "clicks": sum(e["clicks"] for e in events),
        "results_views": sum(e["results_views"] for e in events),
        "results_clicks": sum(e["results_clicks"] for e in events),
        "events": events,
    }


class SwitchIn(BaseModel):
    on: bool


@admin_router.post("/sponsored/enabled")
def sponsored_switch(body: SwitchIn, session: SessionDep) -> dict[str, bool]:
    """Show or hide every sponsored placement on the site at once (the picks are kept)."""
    session.merge(FlagRow(key="sponsored", on=body.on))
    return {"enabled": body.on}


@admin_router.put("/sponsored")
def sponsored_save(items: list[SponsoredIn], session: SessionDep) -> dict[str, int]:
    """Replace every placement; list order is the order within each side."""
    if len(items) > (most := get_settings().api.max_sponsored):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"at most {most} sponsored events")
    ids = [i.event_id for i in items]
    if len(set(ids)) != len(ids):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "an event can be sponsored once")
    known = set(session.scalars(select(EventRow.id).where(EventRow.id.in_(ids))))
    # Placements whose event retention already deleted may stay; new ones must exist.
    kept = set(session.scalars(select(SponsoredRow.event_id)))
    if missing := [i for i in ids if i not in known and i not in kept]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown events: {', '.join(missing)}")
    session.execute(delete(SponsoredRow))
    positions = {"right": 0, "left": 0}
    for i in items:
        session.add(
            SponsoredRow(
                event_id=i.event_id, side=i.side, position=positions[i.side], until=i.until, placement=i.placement
            )
        )
        positions[i.side] += 1
    session.flush()
    paid.sync(session)  # events.is_sponsored
    return {"saved": len(items)}
