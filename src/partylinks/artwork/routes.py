"""Serving drawn cards: ``/artwork/{id}`` (1280 wide) and ``/artwork/{id}/640.webp`` / ``1280.webp``."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import undefer

from partylinks.artwork.models import EventArtworkRow

router = APIRouter(tags=["events"], include_in_schema=False)
SIZES = {640: "webp_640", 1280: "webp_1280"}


def _card(request: Request, card_id: int, width: int) -> Response:
    column = SIZES.get(width)
    if column is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such size")
    with request.app.state.db.session(write=False) as session:
        card = session.scalar(
            select(EventArtworkRow)
            .options(undefer(getattr(EventArtworkRow, column)))
            .where(EventArtworkRow.id == card_id)
        )
        if card is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such artwork")
        etag = f'"a{card.id}-{card.signature[:12]}-{width}"'
        # An hour, not immutable: the card is redrawn when the event changes, and goes once a real picture comes.
        headers = {"Cache-Control": "public, max-age=3600", "ETag": etag}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
        return Response(getattr(card, column), media_type="image/webp", headers=headers)


@router.get("/artwork/{card_id}")
def artwork(card_id: int, request: Request) -> Response:
    return _card(request, card_id, 1280)


@router.get("/artwork/{card_id}/{width}.webp")
def artwork_sized(card_id: int, width: int, request: Request) -> Response:
    return _card(request, card_id, width)
