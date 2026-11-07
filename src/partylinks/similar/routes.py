"""GET /artists/similar?name=: "if you liked X", the artist page's row of artists like it (``similar.index``)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel

from partylinks.config import Settings
from partylinks.db import Database
from partylinks.similar.index import index, like

router = APIRouter(tags=["artists"])
MAX_NAME = 100


class SimilarArtistOut(BaseModel):
    name: str
    events: int  # its upcoming events


class SimilarOut(BaseModel):
    artists: list[SimilarArtistOut]


@router.get("/artists/similar", response_model=SimilarOut)
def similar(request: Request, name: Annotated[str, Query(min_length=1, max_length=MAX_NAME)]) -> Response:
    cache = request.app.state.response_cache
    db: Database = request.app.state.db
    cfg: Settings = request.app.state.settings

    def body() -> bytes:
        with db.session(write=False) as session:
            found = like(index(session, cfg, request.app.state), cfg, name)
        return SimilarOut(artists=[SimilarArtistOut(name=a.name, events=a.events) for a in found]).model_dump_json().encode()

    return Response(cache.get((request.url.path, request.url.query), body)[0], media_type="application/json")
