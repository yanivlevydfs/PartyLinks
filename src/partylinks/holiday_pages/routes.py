"""GET /holidays/list: the coming Israeli holidays with their dates and event counts (the /holidays page, a
holiday page's date filter and the home row of the next holiday)."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from partylinks.config import Settings
from partylinks.db import Database
from partylinks.holiday_pages.core import counts, upcoming

router = APIRouter(tags=["holidays"])


class HolidayOut(BaseModel):
    key: str
    he: str
    en: str
    start: date  # local dates, the eve to the last day (or today, once it has begun)
    end: date
    events: int
    page: bool  # enough events for /holiday/<key>


class HolidaysOut(BaseModel):
    holidays: list[HolidayOut]  # soonest first


def holidays_out(session: Session, cfg: Settings) -> HolidaysOut:
    holidays = upcoming(cfg, datetime.now(ZoneInfo(cfg.timezone)).date())
    n = counts(session, holidays)
    return HolidaysOut(holidays=[
        HolidayOut(key=h.key, he=h.he, en=h.en, start=h.start, end=h.end, events=n[h.key],
                   page=n[h.key] >= cfg.holiday_pages.min_events)
        for h in holidays
    ])  # fmt: skip


@router.get("/holidays/list", response_model=HolidaysOut)
def holidays(request: Request) -> Response:
    """The app's data; /holidays itself is the page (holiday_pages.pages)."""
    cache = request.app.state.response_cache
    db: Database = request.app.state.db

    def body() -> bytes:
        with db.session(write=False) as session:
            return holidays_out(session, request.app.state.settings).model_dump_json().encode()

    return Response(cache.get((request.url.path, request.url.query), body)[0], media_type="application/json")
