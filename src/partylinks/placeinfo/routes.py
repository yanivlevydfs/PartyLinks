"""GET /places/info/{id}: the place page's details. GET /place-photos/{slug}/{n}.webp: a venue's stored photo."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import select

from partylinks.db import Database, EventRow
from partylinks.placecards import IS_PLACE
from partylinks.placeinfo.details import place_details
from partylinks.placeinfo.models import OntopoPhotoRow

router = APIRouter(tags=["places"])


@router.get("/places/info/{event_id:path}")
def info(event_id: str, request: Request, lang: str = "he") -> dict:
    db: Database = request.app.state.db
    with db.session(write=False) as session:
        row = session.scalar(select(EventRow).where(EventRow.id == event_id, IS_PLACE))
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such place")
        return place_details(session, row, request.app.state.settings.place_info, "en" if lang == "en" else "he")


@router.get("/place-photos/{slug}/{n}.webp", response_class=Response)
def photo(slug: str, n: int, request: Request) -> Response:
    db: Database = request.app.state.db
    with db.session(write=False) as session:
        data = session.scalar(select(OntopoPhotoRow.webp).where(OntopoPhotoRow.slug == slug, OntopoPhotoRow.n == n))
    if data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no photo")
    return Response(content=data, media_type="image/webp", headers={"Cache-Control": "public, max-age=604800"})
