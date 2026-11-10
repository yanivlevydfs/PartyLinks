"""``GET /admin/api/insights/features`` and ``/insights/quality``: the admin's features and quality tabs."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Query, Request

from partylinks.admin import AdminDep
from partylinks.admin_insights import stats

router = APIRouter(prefix="/admin/api/insights", tags=["admin"], dependencies=[AdminDep])


def _read(request: Request, fn: Any, *args: Any) -> Any:
    with request.app.state.db.session(write=False) as session:
        return fn(session, *args)


@router.get("/features")
async def features(request: Request, days: int = Query(7, ge=1, le=90)) -> dict[str, Any]:
    return await asyncio.to_thread(_read, request, stats.features, days)


@router.get("/quality")
async def quality(request: Request) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_read, request, stats.quality)
