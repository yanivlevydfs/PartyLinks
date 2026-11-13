"""GET /artists/tags: every upcoming lineup artist (by its casefolded name, the key of the categories' lineup
facet) with its genres and categories ("g": raw genres, umbrella genres and filter families, most common
first) and its places ("w": a region key in Israel, an ISO country code abroad)."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any

from fastapi import APIRouter, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from partylinks.config import Settings
from partylinks.db import EventRow, upcoming_filter
from partylinks.enrichment import ABROAD_AREA, OUTSIDE_AREA

router = APIRouter(tags=["artists"])


def _kept(counts: Counter[str], events: int, share: float) -> list[str]:
    """Values on at least ``share`` of the events, most common first; the top one always."""
    ranked = counts.most_common()
    return [v for i, (v, n) in enumerate(ranked) if i == 0 or n >= share * events]


def tags(
    session: Session, cfg: Settings, region_keys: set[str], listed: set[str] | None = None
) -> dict[str, dict[str, list[str]]]:
    """``listed``: the only keys the index lists (verified artists only), None for all of them."""
    rows = session.execute(
        select(EventRow.lineup, EventRow.genres, EventRow.in_target_city, EventRow.area, EventRow.country).where(
            upcoming_filter(), EventRow.is_primary.is_(True), EventRow.lineup.is_not(None)
        )
    ).all()
    regions = region_keys - {ABROAD_AREA, OUTSIDE_AREA}
    events: Counter[str] = Counter()
    genres: dict[str, Counter[str]] = defaultdict(Counter)
    places: dict[str, Counter[str]] = defaultdict(Counter)
    for lineup, row_genres, in_city, area, country in rows:
        tagged = cfg.enrichment.with_parents(row_genres or [])
        where = country or ("tel_aviv" if in_city else area if area in regions else None)
        for key in (
            {str(n).casefold() for n in lineup or [] if n}
            if listed is None
            else {k for n in lineup or [] if n and (k := str(n).casefold()) in listed}
        ):
            events[key] += 1
            genres[key].update(tagged)
            if where:
                places[key][where] += 1
    share = cfg.artist_filter.min_share
    return {key: {"g": _kept(genres[key], n, share), "w": _kept(places[key], n, share)} for key, n in events.items()}


def tags_body(state: Any) -> bytes:
    """GET /artists/tags's JSON (also read by the artists index, partylinks.facet_list)."""
    with state.db.session(write=False) as session:
        listed = set(state.service.artists.profiles()) if state.settings.site.artists_verified_only else None
        found = tags(session, state.settings, state.service.enricher.areas.region_keys, listed)
    return json.dumps(found, ensure_ascii=False, separators=(",", ":")).encode()


@router.get("/artists/tags")
def artist_tags(request: Request) -> Response:
    state: Any = request.app.state
    return Response(
        state.response_cache.get((request.url.path, request.url.query), lambda: tags_body(state))[0],
        media_type="application/json",
    )
