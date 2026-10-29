"""Session cookies: a random token in the browser, only its SHA-256 in ``user_sessions``."""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from partylinks.accounts.models import SessionRow, UserRow
from partylinks.db import utcnow

COOKIE = "spotx_session"
ROLL_AFTER = timedelta(days=1)  # move the expiry forward at most once a day, not on every request


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def start(session: Session, response: Response, user_id: str, days: int, secure: bool) -> None:
    token = secrets.token_urlsafe(32)
    session.add(SessionRow(user_id=user_id, token_hash=_hash(token), expires_at=utcnow() + timedelta(days=days)))
    response.set_cookie(COOKIE, token, max_age=days * 86400, httponly=True, secure=secure, samesite="lax", path="/")


def end(session: Session, request: Request, response: Response) -> None:
    if token := request.cookies.get(COOKIE):
        session.execute(delete(SessionRow).where(SessionRow.token_hash == _hash(token)))
    response.delete_cookie(COOKIE, path="/")


def revoke_all(session: Session, user_id: str) -> None:
    session.execute(delete(SessionRow).where(SessionRow.user_id == user_id))


def user_for(session: Session, token: str | None, days: int) -> UserRow | None:
    """The signed-in user, or None: no cookie, an unknown or expired token, or a blocked user."""
    if not token:
        return None
    row = session.execute(
        select(SessionRow, UserRow).join(UserRow, UserRow.id == SessionRow.user_id).where(SessionRow.token_hash == _hash(token))
    ).first()
    if row is None:
        return None
    seen, user = row
    now = utcnow()
    if seen.expires_at <= now or user.blocked_at is not None:
        return None
    if now - seen.last_seen_at > ROLL_AFTER:
        seen.last_seen_at, seen.expires_at = now, now + timedelta(days=days)
    return user


def optional_user(request: Request) -> UserRow | None:
    """The signed-in user, detached (its own short session: routes open theirs in the same thread,
    as the rest of the app does). Re-read on every request, so a block signs the user out at once."""
    token = request.cookies.get(COOKIE)
    if not token or not request.app.state.settings.accounts.enabled:
        return None
    with request.app.state.db.session() as session:
        user = user_for(session, token, request.app.state.settings.accounts.session_days)
        if user is not None:
            session.flush()
            session.expunge(user)
    return user


def current_user(user: Annotated[UserRow | None, Depends(optional_user)]) -> UserRow:
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "sign in")
    return user


UserDep = Annotated[UserRow, Depends(current_user)]
OptionalUserDep = Annotated[UserRow | None, Depends(optional_user)]
