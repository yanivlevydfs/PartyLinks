"""Artists like an artist ("if you liked X"), read from our upcoming listings: artists who share its lineups,
its genres and its venues, each weighted by ``similar`` settings. Only artists with upcoming events are
suggested, so every suggestion leads to a show.

The index (every lineup name's genres, venues, co-artists and event count) is built from one query and kept
``ttl_seconds`` per process; a lookup is a scan of that in-memory index (a few thousand artists).
"""

from __future__ import annotations

import math
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from partylinks.artist_links.models import name_key
from partylinks.config import Settings
from partylinks.db import EventRow, upcoming_filter


@dataclass
class Artist:
    name: str  # its most used spelling
    events: int = 0
    genres: Counter[str] = field(default_factory=Counter)
    venues: set[str] = field(default_factory=set)
    with_: Counter[str] = field(default_factory=Counter)  # co-artists' keys -> shared lineups


_lock = threading.Lock()  # one build at a time per process


def build(session: Session) -> dict[str, Artist]:
    """Every upcoming lineup artist by its key (artist_links.name_key)."""
    rows = session.execute(
        select(EventRow.lineup, EventRow.genres, EventRow.venue_slug)
        .where(upcoming_filter(), EventRow.is_primary.is_(True), EventRow.lineup.is_not(None))
    ).all()
    spelled: dict[str, Counter[str]] = defaultdict(Counter)
    out: dict[str, Artist] = {}
    for lineup, genres, venue in rows:
        keys = {k: n for n in lineup or [] if (k := name_key(n))}
        for key, name in keys.items():
            spelled[key][name] += 1
            a = out.setdefault(key, Artist(name))
            a.events += 1
            a.genres.update(genres or [])
            if venue:
                a.venues.add(venue)
            a.with_.update(k for k in keys if k != key)
    for key, a in out.items():
        a.name = spelled[key].most_common(1)[0][0]
    return out


def index(session: Session, cfg: Settings, state: Any) -> dict[str, Artist]:
    """The app's index (kept on ``state``, the app's state), rebuilt once ``ttl_seconds`` old."""
    with _lock:
        built, found = getattr(state, "similar_index", (0.0, {}))
        if time.monotonic() - built > cfg.similar.ttl_seconds:
            built, found = time.monotonic(), build(session)
            state.similar_index = (built, found)
        return found


def _cosine(a: Counter[str], b: Counter[str]) -> float:
    dot = sum(n * b[g] for g, n in a.items() if g in b)
    return dot / (math.sqrt(sum(n * n for n in a.values())) * math.sqrt(sum(n * n for n in b.values())) or 1)


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def like(artists: dict[str, Artist], cfg: Settings, name: str) -> list[Artist]:
    """The artists most like ``name`` (best first), up to ``similar.limit``; [] for an unknown artist."""
    s = cfg.similar
    me = artists.get(name_key(name))
    if not s.enabled or me is None:
        return []
    key = name_key(name)
    scored = []
    for k, other in artists.items():
        if k == key:
            continue
        score = (s.lineup_weight * me.with_[k] + s.genre_weight * _cosine(me.genres, other.genres)
                 + s.venue_weight * _jaccard(me.venues, other.venues))  # fmt: skip
        if score >= s.min_score:
            scored.append((score, other.events, other))
    scored.sort(key=lambda x: (-x[0], -x[1], x[2].name))
    return [a for _, _, a in scored[: s.limit]]
