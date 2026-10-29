"""The admin's email tab: is sending set up, the switches, the last runs, a test send, a preview."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select

from partylinks.accounts.models import UserRow
from partylinks.accounts.sessions import optional_user
from partylinks.admin import AdminDep
from partylinks.mail import jobs
from partylinks.mail import send as mailer
from partylinks.mail.models import EmailRunRow
from partylinks.mail.render import TEXT, Section, render

router = APIRouter(prefix="/admin/api", tags=["admin"], dependencies=[AdminDep], include_in_schema=False)
Kind = Literal["welcome", "digest"]


class SwitchesIn(BaseModel):
    welcome: bool | None = None
    instant: bool | None = None
    digest: bool | None = None


def _parts(request: Request, session: Any, user: UserRow, kind: str) -> tuple[dict[str, Any], str]:
    settings = request.app.state.settings
    if kind == "welcome":
        return jobs.welcome_parts(settings, user), "all"
    return jobs.digest_parts(settings, session, user)[0], "digest"


@router.get("/email")
def email_state(request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    with request.app.state.db.session() as session:
        s = jobs.switches(session)
        runs = session.scalars(select(EmailRunRow).order_by(EmailRunRow.id.desc()).limit(20)).all()
        return {
            "ready": mailer.ready(settings), "key": settings.mail.resend_api_key is not None,
            "from": settings.mail.from_address, "digest_cron": settings.mail.digest_cron, "timezone": settings.timezone,
            "switches": {"welcome": s.welcome, "instant": s.instant, "digest": s.digest},
            "runs": [
                {"kind": r.kind, "started_at": r.started_at, "finished_at": r.finished_at, "sent": r.sent,
                 "failed": r.failed, "skipped": r.skipped, "error": r.error}
                for r in runs
            ],
        }  # fmt: skip


@router.post("/email/switches")
def set_switches(body: SwitchesIn, request: Request) -> dict[str, bool]:
    with request.app.state.db.session() as session:
        s = jobs.switches(session)
        for kind in ("welcome", "instant", "digest"):
            if (value := getattr(body, kind)) is not None:
                setattr(s, kind, value)
        return {"welcome": s.welcome, "instant": s.instant, "digest": s.digest}


@router.get("/email/preview", response_class=HTMLResponse)
def preview(request: Request, user_id: str, kind: Kind = "digest") -> HTMLResponse:
    """The email as this user would get it now; nothing is sent."""
    settings = request.app.state.settings
    with request.app.state.db.session(write=False) as session:
        user = session.get(UserRow, user_id)
        if user is None:
            raise HTTPException(404, "no such user")
        parts, _ = _parts(request, session, user, kind)
        html = render(settings, kind=kind, lang=user.locale, name=user.name, unsubscribe_url="#", **parts)[1]
    # Shown in the admin in a sandboxed frame; nothing in it may run or load our pages' scripts.
    return HTMLResponse(
        html,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; img-src https: data:; style-src 'unsafe-inline'",
        },
    )


@router.post("/email/test")
def test_send(request: Request, kind: Kind = "digest", user_id: str | None = None) -> dict[str, Any]:
    """Sends one email of ``kind`` to the signed-in admin (or, with the token, to ``user_id``)."""
    settings = request.app.state.settings
    if not mailer.ready(settings):
        raise HTTPException(409, "email is not set up (PARTYLINKS_MAIL__RESEND_API_KEY)")
    me = optional_user(request)
    target = user_id or (me.id if me else None)
    if target is None:
        raise HTTPException(400, "sign in with Google, or pass user_id")
    started = jobs.utcnow()
    with request.app.state.db.session(write=False) as session:
        user = session.get(UserRow, target)
        if user is None:
            raise HTTPException(404, "no such user")
        parts, stop = _parts(request, session, user, kind)
        if kind == "digest" and not any(s.events for s in parts["sections"]):
            t = TEXT[user.locale if user.locale in TEXT else "he"]
            parts["sections"] = [Section(t["digest_weekend"], [])]
        message = jobs._message(settings, user, kind, stop, **parts)
    done = jobs._send(settings, request.app.state.db, "test", f"test:{started.timestamp():.0f}", [message], 0, started)
    return {"sent": bool(done)}
