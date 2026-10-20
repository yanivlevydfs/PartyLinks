"""Cross-listing de-duplication.

The same party is often listed several times: one source has a page per promoter
(GO-OUT) or per entry time (ZYGO), and the party is sold on several platforms
(Eventer + GO-OUT, Tickchak linking to the seller). Every listing is kept - each
is a real purchase link with its own prices - but listings of one event share a
``group_id`` and exactly one of them is ``is_primary``. The API shows primaries
by default and exposes the rest as alternatives.

Two listings match when they are on the same local date and either one links to
the other's page, or all of these hold: similar normalized titles, start times
within ``max_start_gap_minutes`` and compatible locations.
"""

from __future__ import annotations

import logging
import math
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from partylinks.config import DedupSettings
from partylinks.db import EventRow, TicketRow, bulk_update_by_id

log = logging.getLogger(__name__)

_GROUP_FIELDS = (
    "group_id", "is_primary", "group_size", "group_min_price", "group_max_price",
    "group_has_free_entry", "group_entry_type", "best_listing_id", "group_free_entry_until",
)  # fmt: skip


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


_KINDS = {"free": ("free",), "paid": ("paid",), "free_and_paid": ("free", "paid")}
_DATE_TOKEN = re.compile(r"\b\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?\b")
_NON_WORD = re.compile(r"[\W_]+", re.UNICODE)


@dataclass
class _Listing:
    id: str
    source: str
    title_key: str
    start: datetime | None
    lat: float | None
    lng: float | None
    venue_slug: str | None
    url_key: str
    links_to: str | None
    tiers: int
    first_seen: datetime
    min_price: Decimal | None
    max_price: Decimal | None
    entry_type: str
    has_free_entry: bool
    free_entry_until: datetime | None = None


def title_key(title: str | None, stopwords: set[str]) -> str:
    """Title reduced to its words: no dates (22.10), emojis, punctuation or stopwords; sorted."""
    text = _DATE_TOKEN.sub(" ", (title or "").lower())
    words = [w for w in _NON_WORD.sub(" ", text).split() if w not in stopwords and not w.isdigit()]
    return " ".join(sorted(set(words)))


def url_key(url: str | None) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    return f"{parsed.netloc.lower().removeprefix('www.')}{parsed.path.rstrip('/').lower()}"


def _distance_m(a: _Listing, b: _Listing) -> float | None:
    if None in (a.lat, a.lng, b.lat, b.lng):
        return None
    lat1, lng1, lat2, lng2 = map(math.radians, (a.lat, a.lng, b.lat, b.lng))  # type: ignore[arg-type]
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 6_371_000 * 2 * math.asin(math.sqrt(h))


class Deduplicator:
    def __init__(self, settings: DedupSettings) -> None:
        self.settings = settings
        self._stopwords = {w.lower() for w in settings.title_stopwords}
        self._priority = {name: rank for rank, name in enumerate(settings.source_priority)}

    def same_event(self, a: _Listing, b: _Listing) -> bool:
        if a.links_to and a.links_to == b.url_key or b.links_to and b.links_to == a.url_key:
            return True
        if not a.title_key or not b.title_key:
            return False
        matcher = SequenceMatcher(None, a.title_key, b.title_key)
        threshold = self.settings.title_similarity
        if matcher.real_quick_ratio() < threshold or matcher.quick_ratio() < threshold or matcher.ratio() < threshold:
            return False
        if a.start and b.start and abs((a.start - b.start).total_seconds()) > self.settings.max_start_gap_minutes * 60:
            return False
        distance = _distance_m(a, b)
        if distance is not None:
            if distance <= self.settings.max_distance_meters:
                return True
            s = self.settings
            gap = abs((a.start - b.start).total_seconds()) if a.start and b.start else None
            return (
                gap is not None
                and gap <= s.strong_max_start_gap_minutes * 60
                and matcher.ratio() >= s.strong_title_similarity
                and distance <= s.strong_max_distance_meters
            )
        if a.venue_slug and b.venue_slug:
            return a.venue_slug == b.venue_slug
        return True  # one side has no location: title + time decide

    def _primary_rank(self, item: _Listing) -> tuple[Any, ...]:
        return (self._priority.get(item.source, len(self._priority)), -item.tiers, item.first_seen, item.id)

    def _group_rows(self, members: list[_Listing]) -> list[dict[str, Any]]:
        """Group id, primary flag and the best deal across all members, written to every member."""
        primary = min(members, key=self._primary_rank)
        priced = [m for m in members if m.min_price is not None]
        best = min(priced, key=lambda m: (m.min_price, self._primary_rank(m))) if priced else primary
        kinds = {k for m in members for k in _KINDS.get(m.entry_type, ())}
        entry_type = (
            "free_and_paid" if kinds == {"free", "paid"} else next(iter(kinds)) if kinds else "unknown"
        )
        maxima = [m.max_price for m in members if m.max_price is not None]
        shared = {
            "group_id": primary.id,
            "group_size": len(members),
            "group_min_price": best.min_price,
            "group_max_price": max(maxima) if maxima else None,
            "group_has_free_entry": any(m.has_free_entry for m in members),
            "group_free_entry_until": _latest_free_until(members),
            "group_entry_type": entry_type,
            "best_listing_id": best.id,
        }
        return [{"id": m.id, "is_primary": m.id == primary.id, **shared} for m in members]

    def regroup(self, session: Session) -> int:
        """Recompute group_id / is_primary for all events. Returns the number of merged groups."""
        rows = session.execute(
            select(
                EventRow.id, EventRow.source, EventRow.title, EventRow.local_date, EventRow.start_at,
                EventRow.latitude, EventRow.longitude, EventRow.venue_slug, EventRow.url, EventRow.extra,
                EventRow.first_seen_at, EventRow.min_price, EventRow.max_price, EventRow.entry_type,
                EventRow.has_free_entry, EventRow.free_entry_until,
            )
        ).all()  # fmt: skip
        tier_counts = _tier_counts(session)
        by_day: dict[Any, list[_Listing]] = defaultdict(list)
        updates: list[dict[str, Any]] = []
        for r in rows:
            listing = _Listing(
                id=r.id, source=r.source, title_key=title_key(r.title, self._stopwords), start=r.start_at,
                lat=r.latitude, lng=r.longitude, venue_slug=r.venue_slug, url_key=url_key(r.url),
                links_to=url_key(_str((r.extra or {}).get("purchase_url") if isinstance(r.extra, dict) else None)) or None,
                tiers=tier_counts.get(r.id, 0), first_seen=r.first_seen_at,
                min_price=r.min_price, max_price=r.max_price, entry_type=r.entry_type,
                has_free_entry=r.has_free_entry, free_entry_until=r.free_entry_until,
            )  # fmt: skip
            if r.local_date is None:
                updates += self._group_rows([listing])
            else:
                by_day[r.local_date].append(listing)

        merged = 0
        for listings in by_day.values():
            parent = {item.id: item.id for item in listings}

            def find(x: str) -> str:
                while parent[x] != x:
                    parent[x] = parent[parent[x]]
                    x = parent[x]
                return x

            for i, a in enumerate(listings):
                for b in listings[i + 1 :]:
                    if find(a.id) != find(b.id) and self.same_event(a, b):
                        parent[find(a.id)] = find(b.id)
            groups: dict[str, list[_Listing]] = defaultdict(list)
            for item in listings:
                groups[find(item.id)].append(item)
            for members in groups.values():
                merged += len(members) > 1
                updates += self._group_rows(members)
        # Write only rows whose grouping changed: short writer lock, no churn, no deadlocks.
        current = {r.id: r for r in session.execute(select(EventRow.id, *(getattr(EventRow, c) for c in _GROUP_FIELDS)))}
        changed = [
            u for u in updates if u["id"] in current and any(getattr(current[u["id"]], c) != u[c] for c in _GROUP_FIELDS)
        ]
        bulk_update_by_id(session, EventRow, changed)
        log.info("dedup: %d listings, %d merged groups", len(rows), merged)
        return merged


def _tier_counts(session: Session) -> dict[str, int]:
    rows = session.execute(select(TicketRow.event_id, func.count()).group_by(TicketRow.event_id))
    return {event_id: count for event_id, count in rows}


def _latest_free_until(members: list[_Listing]) -> datetime | None:
    """Free entry for the event lasts as long as its longest-lasting free listing (None = open-ended)."""
    free = [m.free_entry_until for m in members if m.has_free_entry]
    if not free or any(until is None for until in free):
        return None
    return max(u for u in free if u is not None)
