"""``POST /api/contact``: no sign-in (browsing never needs one), rate-limited, our pages only, a hidden field
for bots. Saved for the admin's "requests" tab, never emailed (the owner, 2026-11-13: save on sending).
The admin's ``/admin/api/contact-messages`` lists, sets status and deletes."""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, StringConstraints
from sqlalchemy import select

from partylinks.accounts.guard import SameOrigin, limited
from partylinks.admin import AdminDep
from partylinks.api import SessionDep
from partylinks.config import Settings
from partylinks.contact.models import ContactMessageRow

log = logging.getLogger(__name__)
router = APIRouter(tags=["contact"])
admin = APIRouter(prefix="/admin/api/contact-messages", tags=["admin"], dependencies=[AdminDep])


class ContactIn(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)]
    email: EmailStr
    subject: Annotated[str, StringConstraints(strip_whitespace=True, max_length=160)] = ""
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=10000)]
    page: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] = ""  # the link they mention
    lang: Literal["he", "en"] = "he"
    website: str = ""  # hidden from people: a bot that fills it gets a quiet "thanks" and nothing is saved


def _out(r: ContactMessageRow) -> dict[str, Any]:
    return {"id": r.id, "name": r.name, "email": r.email, "subject": r.subject, "page": r.page,
            "message": r.message, "lang": r.lang, "status": r.status, "created_at": r.created_at}  # fmt: skip


@router.post(
    "/api/contact", status_code=status.HTTP_201_CREATED, dependencies=[limited("contact", "contact"), SameOrigin]
)
def contact(body: ContactIn, request: Request, session: SessionDep) -> dict[str, int]:
    cfg: Settings = request.app.state.settings
    if not cfg.contact.enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "the contact form is closed")
    if body.website:
        log.info("contact message dropped: the hidden field was filled")
        return {"id": 0}
    if len(body.message) > cfg.contact.message_max:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"message: at most {cfg.contact.message_max} characters"
        )
    if body.page and not body.page.lower().startswith(("https://", "http://")):  # the admin's tab links it
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "page: not a web link")
    r = ContactMessageRow(name=body.name, email=body.email, subject=body.subject or None, page=body.page or None,
                          message=body.message, lang=body.lang)  # fmt: skip
    session.add(r)
    session.flush()
    log.info("contact message %s saved", r.id)
    return {"id": r.id}


@admin.get("")
def messages(request: Request, session: SessionDep) -> list[dict[str, Any]]:
    """The latest messages, newest first."""
    shown = request.app.state.settings.contact.admin_shown
    return [
        _out(r) for r in session.scalars(select(ContactMessageRow).order_by(ContactMessageRow.id.desc()).limit(shown))
    ]


class StatusIn(BaseModel):
    status: Literal["new", "answered", "closed"]


@admin.patch("/{message_id}")
def set_status(message_id: int, body: StatusIn, session: SessionDep) -> dict[str, Any]:
    r = session.get(ContactMessageRow, message_id)
    if r is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such message")
    r.status = body.status
    return _out(r)


@admin.delete("/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove(message_id: int, session: SessionDep) -> None:
    """Delete a message and the person's details in it (already gone: nothing to do)."""
    if r := session.get(ContactMessageRow, message_id):
        session.delete(r)
        log.info("contact message %s: deleted", message_id)
