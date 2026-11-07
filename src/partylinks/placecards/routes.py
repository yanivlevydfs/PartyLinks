"""GET /places/categories: the places hub (/places), its categories read from the place listings themselves."""

from __future__ import annotations

from collections import defaultdict
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from partylinks.config import Settings
from partylinks.db import Database, EventRow, open_now_filters, upcoming_filter
from partylinks.placecards import IS_PLACE

router = APIRouter(tags=["places"])


class PlaceCategoryOut(BaseModel):
    genre: str
    places: int  # distinct places (one page each) listed in the coming days
    open_now: int


class PlaceCategoriesOut(BaseModel):
    categories: list[PlaceCategoryOut]  # most places first
    open_now: int  # places open at this hour, every category


def place_categories(session: Session, cfg: Settings) -> PlaceCategoriesOut:
    """Every genre a place listing carries (happy hour, kosher, escape rooms...), with its places."""
    is_open = and_(*open_now_filters(ZoneInfo(cfg.timezone)))
    rows = session.execute(
        select(EventRow.url, EventRow.genres, is_open).where(
            upcoming_filter(), EventRow.is_primary.is_(True), EventRow.country.is_(None), IS_PLACE
        )
    ).all()
    places: dict[str, set[str]] = defaultdict(set)
    open_: dict[str, set[str]] = defaultdict(set)
    for url, genres, now in rows:
        for g in genres or []:
            places[g].add(url)
            if now:
                open_[g].add(url)
    cats = [PlaceCategoryOut(genre=g, places=len(urls), open_now=len(open_[g])) for g, urls in places.items()]
    cats.sort(key=lambda c: (-c.places, c.genre))
    return PlaceCategoriesOut(categories=cats, open_now=len({u for urls in open_.values() for u in urls}))


@router.get("/places/categories", response_model=PlaceCategoriesOut)
def categories(request: Request) -> Response:
    cache = request.app.state.response_cache
    db: Database = request.app.state.db

    def body() -> bytes:
        with db.session(write=False) as session:
            return place_categories(session, request.app.state.settings).model_dump_json().encode()

    return Response(cache.get((request.url.path, request.url.query), body)[0], media_type="application/json")
