"""The admin's users tab: search, detail, sign out everywhere, block, role, delete, stats."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from partylinks.accounts import service
from partylinks.accounts.models import SessionRow, UserRow
from partylinks.accounts.sessions import revoke_all
from partylinks.admin import AdminDep
from partylinks.db import utcnow

router = APIRouter(prefix="/admin/api", tags=["admin"], dependencies=[AdminDep], include_in_schema=False)
PAGE = 25
# Per-user counts added by other packages (saved events, alerts): name -> (session, user ids) -> {id: n}.
COUNTS: dict[str, Any] = {}


class RoleIn(BaseModel):
    role: Literal["user", "admin"]


def _row(user: UserRow, live: dict[str, int], extra: dict[str, dict[str, int]]) -> dict[str, Any]:
    return {
        "id": user.id, "name": user.name, "email": user.email, "picture": user.picture, "role": user.role,
        "locale": user.locale, "blocked": user.blocked_at is not None, "digest_opt_in": user.digest_opt_in,
        "created_at": user.created_at, "last_login_at": user.last_login_at, "sessions": live.get(user.id, 0),
        **{name: counts.get(user.id, 0) for name, counts in extra.items()},
    }  # fmt: skip


def _counts(session: Session, ids: list[str]) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    live = {u: n for u, n in session.execute(
        select(SessionRow.user_id, func.count()).where(SessionRow.user_id.in_(ids), SessionRow.expires_at > utcnow())
        .group_by(SessionRow.user_id)
    )}  # fmt: skip
    return live, {name: count(session, ids) for name, count in COUNTS.items()}


def _user(session: Session, user_id: str) -> UserRow:
    user = session.get(UserRow, user_id)
    if user is None:
        raise HTTPException(404, "no such user")
    return user


@router.get("/users")
def list_users(request: Request, q: str = "", page: int = Query(1, ge=1)) -> dict[str, Any]:
    with request.app.state.db.session(write=False) as session:
        where = []
        if q.strip():
            like = f"%{q.strip().lower()}%"
            where.append(or_(UserRow.email.like(like), func.lower(UserRow.name).like(like), cast(UserRow.id, String) == q.strip()))
        total = session.scalar(select(func.count()).select_from(UserRow).where(*where)) or 0
        users = session.scalars(
            select(UserRow).where(*where).order_by(UserRow.created_at.desc()).offset((page - 1) * PAGE).limit(PAGE)
        ).all()
        live, extra = _counts(session, [u.id for u in users])
        return {"total": total, "page": page, "page_size": PAGE, "items": [_row(u, live, extra) for u in users]}


@router.get("/users/stats")
def user_stats(request: Request, days: int = Query(30, ge=1, le=365)) -> dict[str, Any]:
    tz = request.app.state.settings.timezone
    now = utcnow()
    with request.app.state.db.session(write=False) as session:
        count = lambda *where: session.scalar(select(func.count()).select_from(UserRow).where(*where)) or 0
        # Grouped here, not in SQL: the local and test servers have no time zone data.
        zone = ZoneInfo(tz)
        joined = session.scalars(select(UserRow.created_at).where(UserRow.created_at >= now - timedelta(days=days)))
        per_day = sorted(Counter(t.astimezone(zone).date() for t in joined).items())
        return {
            "total": count(), "new_7": count(UserRow.created_at >= now - timedelta(days=7)),
            "new_30": count(UserRow.created_at >= now - timedelta(days=30)),
            "digest": count(UserRow.digest_opt_in.is_(True)), "admins": count(UserRow.role == "admin"),
            "blocked": count(UserRow.blocked_at.is_not(None)),
            "per_day": [{"day": d.isoformat(), "n": n} for d, n in per_day],
            "today": datetime.now(zone).date().isoformat(),
        }  # fmt: skip


@router.get("/users/{user_id}")
def user_detail(user_id: str, request: Request) -> dict[str, Any]:
    with request.app.state.db.session(write=False) as session:
        user = _user(session, user_id)
        live, extra = _counts(session, [user.id])
        return _row(user, live, extra) | {
            "digest_opt_in_at": user.digest_opt_in_at, "blocked_at": user.blocked_at,
            "welcome_sent_at": user.welcome_sent_at, "last_digest_at": user.last_digest_at,
        }  # fmt: skip


@router.post("/users/{user_id}/revoke")
def revoke(user_id: str, request: Request) -> dict[str, bool]:
    with request.app.state.db.session() as session:
        revoke_all(session, _user(session, user_id).id)
    return {"ok": True}


@router.post("/users/{user_id}/block")
def block(user_id: str, request: Request) -> dict[str, bool]:
    with request.app.state.db.session() as session:
        service.block(session, _user(session, user_id).id, True)
    return {"ok": True}


@router.post("/users/{user_id}/unblock")
def unblock(user_id: str, request: Request) -> dict[str, bool]:
    with request.app.state.db.session() as session:
        service.block(session, _user(session, user_id).id, False)
    return {"ok": True}


@router.post("/users/{user_id}/role")
def set_role(user_id: str, body: RoleIn, request: Request) -> dict[str, str]:
    with request.app.state.db.session() as session:
        _user(session, user_id).role = body.role
    return {"role": body.role}


@router.delete("/users/{user_id}")
def remove(user_id: str, request: Request) -> dict[str, bool]:
    with request.app.state.db.session() as session:
        service.delete_user(session, _user(session, user_id).id)
    return {"ok": True}
