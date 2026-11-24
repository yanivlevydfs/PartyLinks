"""Pictures a user sends with an event. Only a real picture (magic bytes and a full decode) of an allowed type
(media.allowed_content_types: never SVG / HTML), big enough and not over media.max_pixels, is kept; it is stored
only as its WebP copies (640 / 960 wide, media.make_variants), so the original, with its EXIF (a phone photo's
GPS position, its camera) never stays on the server. Files go under ``<media.directory>/community/``."""

from __future__ import annotations

import logging
import mimetypes
import os
import secrets
import threading
from datetime import timedelta
from pathlib import Path

from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from partylinks.community.config import CommunitySettings
from partylinks.community.models import CommunityEventRow, CommunityUploadRow
from partylinks.config import MediaSettings
from partylinks.db import utcnow
from partylinks.media import MediaStore, sniff_image_type, webp_only

log = logging.getLogger(__name__)
FOLDER = "community"


class BadPictureError(ValueError):
    pass


def _measure(path: Path, cfg: CommunitySettings, media: MediaSettings) -> tuple[int, int]:
    """(width, height) of a picture that decodes whole, or BadPictureError."""
    try:
        with Image.open(path) as im:
            width, height = im.size
            if width * height > media.max_pixels:
                raise BadPictureError(f"picture: at most {media.max_pixels // 1_000_000} megapixels")
            if min(width, height) < cfg.upload_min_side:
                raise BadPictureError(f"picture: at least {cfg.upload_min_side} pixels on each side")
            im.load()  # a cut-off or forged file fails here, not in the copies
    except BadPictureError:
        raise
    except Exception as exc:  # any decode error: not a picture we keep
        raise BadPictureError("picture: not a readable image") from exc
    return width, height


def store(media: MediaStore, cfg: CommunitySettings, user_id: str, data: bytes) -> CommunityUploadRow:
    """Check and store one picture; the caller adds the returned row to its session."""
    if len(data) > cfg.upload_max_bytes:
        raise BadPictureError(f"picture: at most {cfg.upload_max_bytes // 1_000_000} MB")
    kind = sniff_image_type(data)
    if kind is None or kind not in media.settings.allowed_content_types:
        raise BadPictureError("picture: JPEG, PNG, WebP, GIF, AVIF or HEIC only")
    token = secrets.token_hex(16)
    relative = f"{FOLDER}/{token}{mimetypes.guess_extension(kind) or '.img'}"
    path = media.path_for(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}-{threading.get_ident()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    try:
        width, height = _measure(path, cfg, media.settings)
        if not webp_only(path, media.settings.webp_quality):  # the copies, then the original goes
            raise BadPictureError("picture: could not be converted, try another file")
    except BaseException:
        media.delete_files([relative])  # the original and any copy written: no row claims them
        raise
    log.info("community upload %s from user %s: %dx%d, %d bytes", token, user_id, width, height, len(data))
    return CommunityUploadRow(id=token, user_id=user_id, local_path=relative, width=width, height=height,
                              size_bytes=len(data))  # fmt: skip


def used_pictures(session: Session, user_id: str, but: int | None = None) -> set[str]:
    """The upload ids the user's events name (all but event ``but``)."""
    rows = session.scalars(select(CommunityEventRow).where(CommunityEventRow.user_id == user_id))
    return {p for r in rows if r.id != but for p in (r.data or {}).get("pictures", [])}


def drop(session: Session, uploads: list[CommunityUploadRow]) -> list[str]:
    """Delete upload rows; returns their files, for the caller to pass to ``MediaStore.delete_files`` once the
    transaction committed (a rollback must not leave rows whose files are gone; a published listing's
    event_images row keeps a file it uses)."""
    for u in uploads:
        session.delete(u)
    return [u.local_path for u in uploads]


def drop_unused(session: Session, cfg: CommunitySettings, user_id: str) -> list[str]:
    """The user's pictures that none of their events names, sent over ``unused_upload_hours`` ago (see drop)."""
    cutoff = utcnow() - timedelta(hours=cfg.unused_upload_hours)
    stale = list(session.scalars(select(CommunityUploadRow).where(
        CommunityUploadRow.user_id == user_id, CommunityUploadRow.created_at < cutoff)))  # fmt: skip
    used = used_pictures(session, user_id) if stale else set()
    return drop(session, [u for u in stale if u.id not in used])
