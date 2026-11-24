"""The /submit form's endpoints (signed-in users only, rate-limited, our pages only, a CAPTCHA on every post and
edit): ``/api/community/config``, ``/api/community/uploads``, ``/api/community/events[/{id}]``; the pictures at
``/community/uploads/{id}[/{width}].webp``; a free event's own page ``/community/events/{id}``; and the admin's
``/admin/api/community`` (list, approve, reject, delete). Included by partylinks.api.

Writes open their own session (``db.session()``), so a picture file is deleted only after its row's delete
committed."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, StringConstraints
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from partylinks import seo
from partylinks.accounts.guard import SameOrigin, limited
from partylinks.accounts.models import UserRow
from partylinks.accounts.sessions import OptionalUserDep, UserDep
from partylinks.admin import AdminDep
from partylinks.community import captcha, images, publish
from partylinks.community.config import CommunitySettings
from partylinks.community.models import CommunityEventRow, CommunityUploadRow
from partylinks.community.schemas import EventIn, SubmitIn, problems
from partylinks.config import Settings
from partylinks.db import utcnow
from partylinks.media import VARIANT_WIDTHS
from partylinks.promote.models import PromoteRequestRow
from partylinks.promote.routes import _phone as mobile_number

log = logging.getLogger(__name__)
public = APIRouter(tags=["community"])
admin = APIRouter(prefix="/admin/api/community", tags=["admin"], dependencies=[AdminDep])
DAY = timedelta(days=1)
Status = Literal["pending", "published", "rejected", "removed"]  # models.STATUSES


def _cfg(request: Request) -> CommunitySettings:
    cfg: CommunitySettings = request.app.state.settings.community
    if not cfg.enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "posting events is closed")
    return cfg


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _out(session: Session, row: CommunityEventRow, full: bool = True) -> dict[str, Any]:
    listed = publish.listing(session, row)
    data = row.data or {}
    out: dict[str, Any] = {
        "id": row.id, "status": row.status, "title": data.get("title"), "start_date": data.get("start_date"),
        "venue_name": data.get("venue_name"), "city": data.get("city"), "pictures": data.get("pictures", []),
        "reject_reason": row.reject_reason if row.status in ("rejected", "removed") else None,
        "wants_sponsored": row.wants_sponsored, "event_id": row.event_id,
        "event_path": seo.event_path(listed) if listed else None,
        "created_at": row.created_at, "updated_at": row.updated_at,
    }  # fmt: skip
    if full:
        out["data"] = data
    return out


def _own(session: Session, user: UserRow, row_id: int) -> CommunityEventRow:
    row = session.get(CommunityEventRow, row_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such event")
    return row


def _trusted(session: Session, user: UserRow, cfg: CommunitySettings) -> bool:
    """An admin, or an account with ``trusted_after`` events approved: its events go live without review."""
    if user.role == "admin" or cfg.trusted_after == 0:
        return True
    approved = session.scalar(select(func.count()).select_from(CommunityEventRow).where(
        CommunityEventRow.user_id == user.id, CommunityEventRow.approved_at.is_not(None)))  # fmt: skip
    return (approved or 0) >= cfg.trusted_after


def _checked(request: Request, session: Session, user: UserRow, body: SubmitIn, row_id: int | None) -> EventIn:
    """The admin-set limits and the pictures (the user's own) of a post or an edit; 422 else. The CAPTCHA is
    checked before (``captcha.verify``), outside the session: a DB connection is never held over Cloudflare's answer."""
    settings: Settings = request.app.state.settings
    event = body.event()
    found = problems(event, settings.community, set(settings.enrichment.genres), settings.timezone)
    if event.pictures:
        mine = set(session.scalars(select(CommunityUploadRow.id).where(
            CommunityUploadRow.id.in_(event.pictures), CommunityUploadRow.user_id == user.id)))  # fmt: skip
        if missing := [p for p in event.pictures if p not in mine]:
            found.append(f"pictures: unknown {', '.join(missing)}")
    if event.wants_sponsored:
        try:
            event.sponsor_phone = mobile_number(event.sponsor_phone, settings.promote.phone_region)
        except HTTPException:
            found.append("sponsor_phone: not a valid mobile number")
    if found:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, found)
    log.debug("community: event %s from user %s passed the checks", row_id or "new", user.id)
    return event


def _go_live(request: Request, session: Session, row: CommunityEventRow) -> None:
    try:
        publish.list_event(session, request.app.state.service, request.app.state.settings, row)
    except publish.DroppedError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, [str(exc)]) from exc


def _sponsor(session: Session, user: UserRow, row: CommunityEventRow, event: EventIn) -> None:
    """The "I want a sponsored link" box: one promotion request for the event, in the admin's requests tab."""
    if not event.wants_sponsored or row.promote_request_id:
        return
    snapshot = {"id": publish.event_id(row), "slug": None, "title": event.title, "venue_name": event.venue_name,
                "start_at": event.start_date.isoformat()}  # fmt: skip
    note = f"[community event #{row.id}] {event.sponsor_note}".strip()
    req = PromoteRequestRow(name=user.name, business=event.organizer or None, email=user.email,
                            phone=event.sponsor_phone, events=[snapshot], placements=["unsure"], message=note,
                            lang=user.locale or "he", user_id=user.id)  # fmt: skip
    session.add(req)
    session.flush()
    row.promote_request_id = req.id
    log.info("community event %s: promotion request %s", row.id, req.id)


# --- the form ---


@public.get("/api/community/config")
def form_config(request: Request) -> dict[str, Any]:
    """What the form needs to draw itself (no sign-in: the page explains before asking for one)."""
    settings: Settings = request.app.state.settings
    cfg = settings.community
    return {
        "enabled": cfg.enabled, "captcha_required": cfg.captcha_required, "turnstile_site_key": cfg.turnstile_site_key,
        "event_types": cfg.event_types, "genres": sorted(settings.enrichment.genres),
        "limits": {k: getattr(cfg, k) for k in ("title_max", "description_min", "description_max", "max_tickets",
                                                 "max_images", "max_artists", "max_genres", "max_event_types",
                                                 "max_days_ahead", "upload_max_bytes", "upload_min_side",
                                                 "sponsor_note_max", "max_per_day")},
    }  # fmt: skip


@public.post("/api/community/uploads", status_code=status.HTTP_201_CREATED,
             dependencies=[limited("community-upload", "community_upload"), SameOrigin])  # fmt: skip
def upload(request: Request, user: UserDep, file: Annotated[UploadFile, File()]) -> dict[str, Any]:
    """One picture: checked, converted to WebP, its EXIF gone (images.py)."""
    cfg = _cfg(request)
    media = request.app.state.service.media
    data = file.file.read(cfg.upload_max_bytes + 1)
    with request.app.state.db.session() as session:
        sent = session.scalar(select(func.count()).select_from(CommunityUploadRow).where(
            CommunityUploadRow.user_id == user.id, CommunityUploadRow.created_at > utcnow() - DAY))  # fmt: skip
        if (sent or 0) >= cfg.uploads_per_day:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, f"pictures: at most {cfg.uploads_per_day} a day")
        stale = images.drop_unused(session, cfg, user.id)
    media.delete_files(stale)
    try:
        row = images.store(media, cfg, user.id, data)
    except images.BadPictureError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, [str(exc)]) from exc
    with request.app.state.db.session() as session:
        session.add(row)
    return {"id": row.id, "url": f"/community/uploads/{row.id}.webp", "width": row.width, "height": row.height}


def _picture(request: Request, upload_id: str, width: int) -> FileResponse:
    with request.app.state.db.session(write=False) as session:
        path = session.scalar(select(CommunityUploadRow.local_path).where(CommunityUploadRow.id == upload_id))
    copy = request.app.state.service.media.copy_path(path, width) if path else None
    if copy is None or not copy.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such picture")
    # The id is random and the picture never changes under it.
    return FileResponse(copy, media_type="image/webp", headers={"Cache-Control": "public, max-age=31536000, immutable"})


@public.get("/community/uploads/{upload_id}.webp", include_in_schema=False)
def picture(request: Request, upload_id: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]) -> FileResponse:
    return _picture(request, upload_id, VARIANT_WIDTHS[-1])


@public.get("/community/uploads/{upload_id}/{width}.webp", include_in_schema=False)
def picture_copy(request: Request, upload_id: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")],
                 width: int) -> FileResponse:  # fmt: skip
    if width not in VARIANT_WIDTHS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such size")
    return _picture(request, upload_id, width)


@public.get("/community/events/{row_id}", include_in_schema=False)
def own_page(row_id: int, request: Request) -> RedirectResponse:
    """A free event's listing points here (no seller's page): on to its event page while it is listed."""
    with request.app.state.db.session(write=False) as session:
        row = session.get(CommunityEventRow, row_id)
        listed = publish.listing(session, row) if row else None
        if listed is None:
            raise HTTPException(status.HTTP_410_GONE, "event gone")
        return RedirectResponse(seo.event_path(listed), status.HTTP_302_FOUND)


@public.get("/api/community/events")
def mine(request: Request, user: UserDep) -> list[dict[str, Any]]:
    with request.app.state.db.session(write=False) as session:
        rows = session.scalars(select(CommunityEventRow).where(CommunityEventRow.user_id == user.id)
                               .order_by(CommunityEventRow.id.desc()))  # fmt: skip
        return [_out(session, r, full=False) for r in rows]


@public.get("/api/community/events/{row_id}")
def one(row_id: int, request: Request, user: UserDep) -> dict[str, Any]:
    with request.app.state.db.session(write=False) as session:
        return _out(session, _own(session, user, row_id))


@public.post("/api/community/events", status_code=status.HTTP_201_CREATED,
             dependencies=[limited("community", "community"), SameOrigin])  # fmt: skip
def create(body: SubmitIn, request: Request, user: UserDep) -> dict[str, Any]:
    """Post an event: live at once for a trusted account, else waiting for an admin."""
    cfg = _cfg(request)
    if body.website:
        log.info("community event dropped: the hidden field was filled")
        return {"id": 0, "status": "pending"}
    captcha.verify(cfg, body.captcha_token, _ip(request))
    with request.app.state.db.session() as session:
        today = session.scalar(select(func.count()).select_from(CommunityEventRow).where(
            CommunityEventRow.user_id == user.id, CommunityEventRow.created_at > utcnow() - DAY))  # fmt: skip
        if (today or 0) >= cfg.max_per_day:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, f"events: at most {cfg.max_per_day} a day")
        event = _checked(request, session, user, body, None)
        row = CommunityEventRow(user_id=user.id, data=event.model_dump(mode="json"), status="pending",
                                wants_sponsored=event.wants_sponsored)  # fmt: skip
        session.add(row)
        session.flush()
        if _trusted(session, user, cfg):
            _go_live(request, session, row)
        _sponsor(session, user, row, event)
        log.info("community event %s from user %s: %s", row.id, user.id, row.status)
        return _out(session, row)


@public.put("/api/community/events/{row_id}", dependencies=[limited("community", "community"), SameOrigin])
def edit(row_id: int, body: SubmitIn, request: Request, user: UserDep) -> dict[str, Any]:
    """Change a posted event: a trusted account's listing is updated at once; otherwise the event (rejected
    too) waits for an admin again. One an admin removed stays removed."""
    cfg = _cfg(request)
    if body.website:
        return {"id": 0, "status": "pending"}
    captcha.verify(cfg, body.captcha_token, _ip(request))
    with request.app.state.db.session() as session:
        row = _own(session, user, row_id)
        if row.status == "removed":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "this event was taken down by the site")
        event = _checked(request, session, user, body, row.id)
        row.data, row.updated_at, row.reject_reason = event.model_dump(mode="json"), utcnow(), None
        row.wants_sponsored = row.wants_sponsored or event.wants_sponsored
        if _trusted(session, user, cfg):
            _go_live(request, session, row)
        else:
            publish.unlist(session, row, "pending")
        _sponsor(session, user, row, event)
        log.info("community event %s edited by user %s: %s", row.id, user.id, row.status)
        return _out(session, row)


def _delete(session: Session, row: CommunityEventRow) -> list[str]:
    """Unlist and delete the row, and the pictures no other event of its owner uses; returns their files."""
    publish.unlist(session, row)
    keep = images.used_pictures(session, row.user_id, but=row.id)
    mine = (row.data or {}).get("pictures", [])
    gone = list(session.scalars(select(CommunityUploadRow).where(
        CommunityUploadRow.id.in_([p for p in mine if p not in keep]), CommunityUploadRow.user_id == row.user_id)))  # fmt: skip
    session.delete(row)
    return images.drop(session, gone)


@public.delete("/api/community/events/{row_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[limited("community", "community"), SameOrigin])  # fmt: skip
def remove(row_id: int, request: Request, user: UserDep) -> None:
    with request.app.state.db.session() as session:
        files = _delete(session, _own(session, user, row_id))
        log.info("community event %s deleted by user %s", row_id, user.id)
    request.app.state.service.media.delete_files(files)


# --- the admin ---


@admin.get("")
def review_list(
    request: Request, state: Annotated[Status | None, Query(alias="status")] = None
) -> list[dict[str, Any]]:
    """The latest events (of one status), newest first, with who sent them."""
    shown = request.app.state.settings.community.admin_shown
    with request.app.state.db.session(write=False) as session:
        query = select(CommunityEventRow, UserRow).join(UserRow, UserRow.id == CommunityEventRow.user_id)
        if state:
            query = query.where(CommunityEventRow.status == state)
        found = session.execute(query.order_by(CommunityEventRow.id.desc()).limit(shown)).all()
        return [_out(session, r) | {"user": {"id": u.id, "name": u.name, "email": u.email}} for r, u in found]


def _admin_row(session: Session, row_id: int) -> CommunityEventRow:
    row = session.get(CommunityEventRow, row_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such event")
    return row


@admin.post("/{row_id}/approve")
def approve(row_id: int, request: Request, user: OptionalUserDep) -> dict[str, Any]:
    """List the event; its account is trusted from now on (CommunitySettings.trusted_after)."""
    with request.app.state.db.session() as session:
        row = _admin_row(session, row_id)
        _go_live(request, session, row)
        row.approved_at, row.reviewed_by, row.reject_reason = utcnow(), user.id if user else None, None
        log.info("community event %s approved", row_id)
        return _out(session, row)


class RejectIn(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=2000)]
    take_down: bool = False  # "removed": the owner may not send it again


@admin.post("/{row_id}/reject")
def reject(row_id: int, body: RejectIn, request: Request, user: OptionalUserDep) -> dict[str, Any]:
    most = request.app.state.settings.community.reject_reason_max
    if len(body.reason) > most:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, [f"reason: at most {most} characters"])
    with request.app.state.db.session() as session:
        row = _admin_row(session, row_id)
        publish.unlist(session, row, "removed" if body.take_down else "rejected")
        row.reject_reason, row.reviewed_by = body.reason, user.id if user else None
        log.info("community event %s %s", row_id, row.status)
        return _out(session, row)


@admin.delete("/{row_id}", status_code=status.HTTP_204_NO_CONTENT)
def admin_delete(row_id: int, request: Request) -> None:
    with request.app.state.db.session() as session:
        row = session.get(CommunityEventRow, row_id)
        files = _delete(session, row) if row else []
        log.info("community event %s deleted by an admin", row_id)
    request.app.state.service.media.delete_files(files)
