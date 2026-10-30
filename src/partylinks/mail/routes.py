"""Unsubscribing from an email link. GET shows a confirm page (link scanners open links, so a GET never
changes anything); POST unsubscribes, and is also RFC 8058's one-click from the mail client."""

from __future__ import annotations

from html import escape

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, update

from partylinks.accounts.models import UserRow
from partylinks.alerts.models import AlertRow
from partylinks.db import utcnow
from partylinks.mail import tokens

router = APIRouter(tags=["mail"], include_in_schema=False)
TEXT = {
    "ask": ("להפסיק לקבל את המיילים האלה?", "Stop getting these emails?"),
    "button": ("כן, להפסיק", "Yes, stop them"),
    "done": ("הוסרת. לא נשלח לך עוד מיילים כאלה.", "Done. You won't get these emails any more."),
    "account": ("אפשר לשנות את זה בכל עת בחשבון שלך.", "You can change this any time in your account."),
    "bad": ("הקישור לא תקין.", "This link isn't valid."),
    "manage": ("אפשר לנהל את המיילים בחשבון שלך.", "You can manage emails in your account."),
}
PAGE = """<!doctype html><html lang="he" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>SPOTX</title><style>body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#06040b;color:#f7f2ff;
font:16px/1.6 system-ui,sans-serif;text-align:center;padding:24px}}button{{font:inherit;font-weight:700;border:0;border-radius:999px;
padding:12px 24px;background:#ff2e93;color:#0a0612;cursor:pointer}}a{{color:#19e3ff}}p{{margin:6px 0}}</style></head>
<body><main>{body}</main></body></html>"""


def _page(body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(PAGE.format(body=body), status_code=status, headers={"Cache-Control": "no-store"})


def _message(request: Request, key: str, link: str, status: int = 200) -> HTMLResponse:
    he, en = TEXT[key]
    ah, an = TEXT[link]
    base = request.app.state.settings.site.base_url.rstrip("/")
    return _page(f'<h1>{he}</h1><p dir="ltr" lang="en">{en}</p><p><a href="{base}/account">{ah}</a></p>'
                 f'<p dir="ltr" lang="en">{an}</p>', status)  # fmt: skip


@router.get("/api/email/unsubscribe")
def confirm(request: Request, t: str = "") -> HTMLResponse:
    if tokens.read(request.app.state.settings, t) is None:
        return _message(request, "bad", "manage", 400)
    he, en = TEXT["ask"]
    bh, bn = TEXT["button"]
    body = (f'<h1>{he}</h1><p dir="ltr" lang="en">{en}</p><form method="post" action="?t={escape(t)}">'
            f'<button>{bh} · {bn}</button></form>')  # fmt: skip
    return _page(body)


@router.post("/api/email/unsubscribe")
def unsubscribe(request: Request, t: str = "") -> HTMLResponse:
    found = tokens.read(request.app.state.settings, t)
    if found is None:
        return _message(request, "bad", "manage", 400)
    user_id, kind = found
    with request.app.state.db.session() as session:
        if kind in ("digest", "all"):
            session.execute(update(UserRow).where(UserRow.id == user_id).values(
                digest_opt_in=False, digest_opt_in_at=None,
                digest_opt_out_at=func.coalesce(UserRow.digest_opt_out_at, utcnow()),  # the first refusal stays
            ))  # fmt: skip
        if kind in ("instant", "all"):
            session.execute(update(AlertRow).where(AlertRow.user_id == user_id).values(instant=False))
    return _message(request, "done", "account")
