"""Signing in and deleting an account."""

from __future__ import annotations

import secrets

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from partylinks.accounts.models import UserRow
from partylinks.accounts.sessions import revoke_all
from partylinks.db import utcnow

LOCALES = ("he", "en")


class Blocked(Exception):
    pass


class EmailTaken(Exception):
    """The same email on a different Google account: never merged silently."""


def sign_in(
    session: Session, *, sub: str, email: str, name: str, picture: str | None, locale: str, admin_emails: list[str]
) -> tuple[UserRow, bool]:
    """The user of this Google account, created on the first sign-in. Returns (user, is_new)."""
    email = email.strip().lower()
    user = session.scalar(select(UserRow).where(UserRow.google_sub == sub))
    is_new = user is None
    if user is None:
        if session.scalar(select(UserRow.id).where(UserRow.email == email)):
            raise EmailTaken
        user = UserRow(
            id=secrets.token_hex(16), google_sub=sub, email=email, name=(name or email.split("@")[0])[:100],
            picture=picture, locale=locale if locale in LOCALES else "he",
        )  # fmt: skip
        session.add(user)
    if user.blocked_at is not None:
        raise Blocked
    user.email, user.picture, user.last_login_at = email, picture, utcnow()
    if email in {e.strip().lower() for e in admin_emails}:
        user.role = "admin"
    session.flush()
    return user, is_new


def block(session: Session, user_id: str, blocked: bool) -> None:
    user = session.get(UserRow, user_id)
    if user is None:
        return
    user.blocked_at = utcnow() if blocked else None
    if blocked:
        revoke_all(session, user_id)


def delete_user(session: Session, user_id: str) -> None:
    """Sessions, saved events, alerts and their matches go with it (ON DELETE CASCADE)."""
    session.execute(delete(UserRow).where(UserRow.id == user_id))
