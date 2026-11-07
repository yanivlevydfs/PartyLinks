"""GET /top/list: this week's most wanted events and the rising genres (the /top page)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from partylinks.api import EventSummaryOut, _summary_extras
from partylinks.config import Settings
from partylinks.db import Database
from partylinks.weekly_top.core import rising, top

router = APIRouter(tags=["events"])


class RisingOut(BaseModel):
    genre: str
    new: int  # events first listed in the last weekly_top.rising_days


class TopOut(BaseModel):
    events: list[EventSummaryOut]  # best first
    rising: list[RisingOut]


@router.get("/top/list", response_model=TopOut)
def top_list(request: Request) -> Response:
    cache = request.app.state.response_cache
    db: Database = request.app.state.db
    cfg: Settings = request.app.state.settings

    def body() -> bytes:
        now = datetime.now(ZoneInfo(cfg.timezone))
        with db.session(write=False) as session:
            rows = top(session, cfg, now)
            extras = _summary_extras(session, rows, cfg)
            out = TopOut(
                events=[EventSummaryOut.model_validate(r).model_copy(update=extras[r.id]) for r in rows],
                rising=[RisingOut(genre=g, new=n) for g, n in rising(session, cfg, now)],
            )
        return out.model_dump_json().encode()

    return Response(cache.get((request.url.path, request.url.query), body)[0], media_type="application/json")
