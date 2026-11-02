"""Signing in and deleting an account."""

from __future__ import annotations

import secrets
from typing import Literal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from partylinks.accounts.models import UserRow
from partylinks.accounts.sessions import revoke_all
from partylinks.db import utcnow

LOCALES = ("he", "en")


class Blocked(Exception):
    pass


class EmailTaken(Exception):
    """The email belongs to a user already tied to another account of the same provider: never merged."""


Provider = Literal["google", "facebook"]


def sign_in(
    session: Session, *, provider: Provider, sub: str, email: str, name: str, picture: str | None, locale: str,
    admin_emails: list[str],
) -> tuple[UserRow, bool]:
    """The user of this Google / Facebook account, created on the first sign-in. Returns (user, is_new).

    Both providers hand over verified addresses only (Google's email_verified; Facebook sends confirmed
    ones), so a first sign-in with an email we know links to that user: Google and Facebook, one account."""
    column = UserRow.google_sub if provider == "google" else UserRow.facebook_id
    email = email.strip().lower()
    user = session.scalar(select(UserRow).where(column == sub))
    is_new = False
    if user is None:
        user = session.scalar(select(UserRow).where(UserRow.email == email))
        if user is not None and getattr(user, column.key) is not None:
            raise EmailTaken  # a different account of the same provider has this address
        if user is None:
            is_new = True
            user = UserRow(
                id=secrets.token_hex(16), email=email, name=(name or email.split("@")[0])[:100],
                picture=picture, locale=locale if locale in LOCALES else "he",
            )  # fmt: skip
            session.add(user)
        setattr(user, column.key, sub)
    if user.blocked_at is not None:
        raise Blocked
    if email != user.email and session.scalar(select(UserRow.id).where(UserRow.email == email, UserRow.id != user.id)):
        raise EmailTaken  # their address changed to one another account already has
    user.email, user.last_login_at = email, utcnow()
    user.picture = picture or user.picture
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
    """Sessions, saved events, alerts and their matches go with it (ON DELETE CASCADE); its group
    answers and ownerships explicitly (those columns were added later, without a database FK)."""
    from partylinks.plans.routes import forget_user  # local: plans imports accounts

    forget_user(session, user_id)
    session.execute(delete(UserRow).where(UserRow.id == user_id))
