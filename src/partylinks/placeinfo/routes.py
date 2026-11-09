"""GET /places/info/{id}: the place page's details."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from partylinks.db import Database, EventRow
from partylinks.placecards import IS_PLACE
from partylinks.placeinfo.details import place_details

router = APIRouter(tags=["places"])


@router.get("/places/info/{event_id:path}")
def info(event_id: str, request: Request, lang: str = "he") -> dict:
    db: Database = request.app.state.db
    with db.session(write=False) as session:
        row = session.scalar(select(EventRow).where(EventRow.id == event_id, IS_PLACE))
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such place")
        return place_details(session, row, request.app.state.settings.place_info, "en" if lang == "en" else "he")
