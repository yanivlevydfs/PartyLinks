"""Pictures for emails: ``/images/{id}/mail-{w}x{h}.jpg`` and ``/artwork/{id}/mail-{w}x{h}.jpg``.

Mail apps don't crop (Gmail ignores object-fit, Outlook has no background-image) and Gmail's proxy and
Outlook don't reliably draw WebP, so an email gets a JPEG already cut to its frame. Made from the stored
WebP on first request, then served from ``<media>/_mail/``. Under /images/ and /artwork/: static paths,
outside the rate limit, open to the mail apps' image proxies.
"""

from __future__ import annotations

import io
import os
import threading
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import FileResponse
from PIL import Image, ImageOps
from sqlalchemy import select
from sqlalchemy.orm import undefer

from partylinks.artwork.models import EventArtworkRow
from partylinks.db import ImageRow
from partylinks.media import VARIANT_WIDTHS, variant_path

HERO = (1104, 552)  # the lead card's 552x276 frame, at 2x
THUMB = (224, 168)  # a row's 112x84 frame, at 2x
SIZES = {HERO, THUMB}
QUALITY = 82
HEADERS = {"Cache-Control": "public, max-age=604800"}  # a week: an email is opened days after it goes

router = APIRouter(tags=["mail"], include_in_schema=False)


def mail_image(path: str, size: tuple[int, int]) -> str:
    """``/images/12`` -> ``/images/12/mail-224x168.jpg``."""
    return f"{path}/mail-{size[0]}x{size[1]}.jpg"


def crop_jpeg(data: bytes | Path, size: tuple[int, int]) -> bytes:
    """The picture cut to ``size`` around its centre, as JPEG on the email's dark background."""
    with Image.open(data if isinstance(data, Path) else io.BytesIO(data)) as im:
        im.draft("RGB", size)
        picture = ImageOps.exif_transpose(im)
        if picture.mode in ("RGBA", "LA", "PA", "P"):
            rgba = picture.convert("RGBA")
            picture = Image.new("RGB", rgba.size, (13, 8, 24))
            picture.paste(rgba, mask=rgba)
        out = io.BytesIO()
        ImageOps.fit(picture.convert("RGB"), size, Image.Resampling.LANCZOS).save(
            out, "JPEG", quality=QUALITY, optimize=True, progressive=True
        )
    return out.getvalue()


def _size(width: int, height: int) -> tuple[int, int]:
    if (width, height) not in SIZES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such size")
    return width, height


def _serve(request: Request, name: str, size: tuple[int, int], make: Callable[[], bytes | Path]) -> Response:
    # ponytail: _mail/ files aren't claimed by the orphan sweep, so it clears them now and then; the next
    # request makes them again (one resize). Claim them in MediaStore.sweep if that ever costs.
    cached = request.app.state.service.media.path_for(f"_mail/{name}")
    if not cached.is_file():
        try:
            data = crop_jpeg(make(), size)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no picture") from exc
        cached.parent.mkdir(parents=True, exist_ok=True)
        tmp = cached.with_name(f"{cached.name}.{os.getpid()}-{threading.get_ident()}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, cached)  # atomic: a parallel request never reads half a file
    return FileResponse(cached, media_type="image/jpeg", headers=HEADERS)


@router.get("/images/{image_id}/mail-{width}x{height}.jpg")
def stored_picture(image_id: int, width: int, height: int, request: Request) -> Response:
    size = _size(width, height)

    def source() -> bytes | Path:
        with request.app.state.db.session(write=False) as session:
            image = session.scalar(select(ImageRow).where(ImageRow.id == image_id).options(undefer(ImageRow.data)))
            if image is None or image.downloaded_at is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "image not stored")
            if image.data is not None:
                return image.data
            media = request.app.state.service.media
            for relative in (variant_path(image.local_path or "", VARIANT_WIDTHS[-1]), image.local_path or ""):
                if image.local_path and (path := media.path_for(relative)).is_file():
                    return path
        raise HTTPException(status.HTTP_404_NOT_FOUND, "image file missing")

    return _serve(request, f"i{image_id}-{width}x{height}.jpg", size, source)


@router.get("/artwork/{card_id}/mail-{width}x{height}.jpg")
def drawn_picture(card_id: int, width: int, height: int, request: Request) -> Response:
    size = _size(width, height)
    with request.app.state.db.session(write=False) as session:
        signature = session.scalar(select(EventArtworkRow.signature).where(EventArtworkRow.id == card_id))
    if signature is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such artwork")

    def source() -> bytes:
        with request.app.state.db.session(write=False) as session:
            data = session.scalar(select(EventArtworkRow.webp_1280).where(EventArtworkRow.id == card_id))
        if data is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such artwork")
        return data

    # Named by what was drawn: a redrawn card is a new file.
    return _serve(request, f"a{card_id}-{signature[:12]}-{width}x{height}.jpg", size, source)
