"""A search -> events, through the site's own filter builder and ranking, so the bot finds what the site
finds. When nothing is found, the search is widened (the date, then the place) and the answer says so."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, defer, selectinload

from partylinks.bot.core import refs, understand
from partylinks.config import Settings
from partylinks.db import EventRow
from partylinks.search import did_you_mean, relevance

KM_PER_DEGREE = 111.32


@dataclass
class Found:
    rows: list[EventRow]
    total: int
    extras: dict[str, dict[str, Any]] = field(default_factory=dict)  # api._summary_extras: deal, cover
    query: str = ""  # the search that found them (widened: not the one asked)
    widened: str | None = None  # "date" / "place": what was dropped to find anything
    suggestion: str | None = None  # "did you mean", when even the widened search found nothing


def distance_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance (haversine)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def search(
    session: Session,
    cfg: Settings,
    query: str,
    *,
    offset: int = 0,
    limit: int,
    cheap: bool = False,
    near: tuple[float, float] | None = None,
) -> Found:
    """One page of upcoming events for ``query`` (the site's search words). ``near``: (lat, lng), only
    events within ``bot.near_km``, nearest first (cheapest first with ``cheap``)."""
    from partylinks.api import _IS_PLACE, build_event_filters  # local: api imports the bot

    filters = build_event_filters(cfg, q=query or None, anywhere=near is not None)
    # Cheapest first when asked (near or not), then nearest, else the site's search rank.
    order: list[Any] = [EventRow.group_min_price.asc().nulls_last()] if cheap else []
    if near:
        lat, lng = near
        dlat = cfg.bot.near_km / KM_PER_DEGREE
        dlng = dlat / max(math.cos(math.radians(lat)), 0.01)
        # The box first: an index-friendly range, then the circle.
        filters += [EventRow.latitude.between(lat - dlat, lat + dlat), EventRow.longitude.between(lng - dlng, lng + dlng)]
        # Flat-earth distance (in degrees of latitude) squared: true to a few metres at these distances.
        scale = math.cos(math.radians(lat)) ** 2
        dy, dx = EventRow.latitude - lat, EventRow.longitude - lng
        flat = dy * dy + scale * dx * dx
        filters.append(flat <= dlat * dlat)
        order.append(flat)
    if not (cheap or near) and query and (rank := relevance(query, cfg)) is not None:
        order.append(rank)  # the site's search order: title and genre matches first
    # Dated events by day; a place "open today" (a club's every-day listing) after the day's real events.
    order += [EventRow.local_date.asc().nulls_last(), _IS_PLACE, EventRow.start_at.asc().nulls_last(), EventRow.id]
    total = session.scalar(select(func.count()).select_from(EventRow).where(*filters)) or 0
    rows = list(session.scalars(
        select(EventRow).where(*filters).order_by(*order).offset(offset).limit(limit)
        .options(selectinload(EventRow.images), defer(EventRow.description), defer(EventRow.extra))
    ))  # fmt: skip
    return Found(rows, total, refs.extras(session, rows), query)


def search_or_widen(
    session: Session, cfg: Settings, query: str, today: date, *, limit: int, cheap: bool = False,
    near: tuple[float, float] | None = None,
) -> Found:  # fmt: skip
    """``search``, and when it finds nothing: without its date words, then without its place words."""
    found = search(session, cfg, query, limit=limit, cheap=cheap, near=near)
    if found.total or not query:
        return found
    named = understand.kinds(query, cfg, today)
    for kind in ("date", "place"):
        if named[kind] and (wider := understand.without(query, named[kind])):
            again = search(session, cfg, wider, limit=limit, cheap=cheap, near=near)
            if again.total:
                again.widened = kind
                return again
    found.suggestion = did_you_mean(query, cfg, ())
    return found
