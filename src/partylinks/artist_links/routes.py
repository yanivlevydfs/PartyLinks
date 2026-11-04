"""``POST /admin/api/maintenance/artist-links``: how many upcoming artists have links (the maintenance tab)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request

from partylinks.admin import AdminDep
from partylinks.artist_links.lookup import counts

router = APIRouter(prefix="/admin/api", tags=["admin"], dependencies=[AdminDep])


@router.post("/maintenance/artist-links")
async def artist_links_counts(request: Request) -> dict[str, int]:
    return await asyncio.to_thread(counts, request.app.state.db)
