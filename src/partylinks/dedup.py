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
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import parse_qsl, urlparse

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from partylinks.config import DedupSettings
from partylinks.db import EventRow, TicketRow, bulk_update_by_id
from partylinks.venues import PLACE_LISTING

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
_TIME_TOKEN = re.compile(r"\b\d{1,2}:\d{2}\b")
_YEAR = re.compile(r"^(?:19|20)\d\d$")


@dataclass
class _Listing:
    id: str
    source: str
    title_key: str
    start: datetime | None
    lat: float | None
    lng: float | None
    venue_slug: str | None
    city: str | None
    kinds: frozenset[str]
    numbers: frozenset[str]
    url_key: str
    links_to: str | None
    tiers: int
    first_seen: datetime
    min_price: Decimal | None
    max_price: Decimal | None
    entry_type: str
    has_free_entry: bool
    free_entry_until: datetime | None = None
    sold_out: bool = False


def title_key(title: str | None, stopwords: set[str]) -> str:
    """Title reduced to its words: no dates (22.10), emojis, punctuation or stopwords; sorted."""
    text = _DATE_TOKEN.sub(" ", (title or "").lower())
    words = [w for w in _NON_WORD.sub(" ", text).split() if w not in stopwords and not w.isdigit()]
    return " ".join(sorted(set(words)))


def title_numbers(title: str | None) -> frozenset[str]:
    """Numbers that tell variants apart ("גילאי 4-6" / "7-9", "Part 1" / "Part 2"); not dates, hours or years."""
    text = _TIME_TOKEN.sub(" ", _DATE_TOKEN.sub(" ", (title or "").lower()))
    return frozenset(w for w in _NON_WORD.sub(" ", text).split() if w.isdigit() and not _YEAR.match(w))


def url_key(url: str | None) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    # Id params name the page on query-routed sites (MainItemPage.aspx?ItemID=...): without them every
    # tel-aviv.gov.il item shares one key and a link to one of them merges the whole day. Tracking params go.
    ids = sorted(
        f"{k.lower()}={v}" for k, v in parse_qsl(parsed.query)
        if k.lower().endswith("id") and not k.lower().endswith(("clid", "clkid")) and not k.lower().startswith("utm_")
    )  # fmt: skip
    query = "?" + "&".join(ids) if ids else ""
    return f"{parsed.netloc.lower().removeprefix('www.')}{parsed.path.rstrip('/').lower()}{query}"


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
        self._promoter_pages = set(settings.promoter_page_sources)
        self._kind = {g: kind for kind, genres in settings.kinds.items() for g in genres}

    def same_event(self, a: _Listing, b: _Listing, shared_urls: frozenset[str] = frozenset()) -> bool:
        """Name, date, hour, place and kind must all agree; in doubt two listings stay apart."""
        if self._contradict(a, b, kinds=False):
            return False
        # A page several listings share (a site's generic "central" page) is not one event's page.
        if a.links_to and a.links_to == b.url_key and b.url_key not in shared_urls:
            return True
        if b.links_to and b.links_to == a.url_key and a.url_key not in shared_urls:
            return True
        if not a.title_key or not b.title_key:
            return False
        matcher = SequenceMatcher(None, a.title_key, b.title_key)
        threshold = self.settings.title_similarity
        if matcher.real_quick_ratio() < threshold or matcher.quick_ratio() < threshold or matcher.ratio() < threshold:
            return False
        if self._other_kind(a, b, matcher.ratio()):
            return False
        distance = _distance_m(a, b)
        if distance is not None:
            if distance <= self.settings.max_distance_meters:
                return True
            s = self.settings
            gap = abs((a.start - b.start).total_seconds()) if a.start and b.start else None
            window = s.party_max_start_gap_minutes if self._party(a, b, matcher.ratio()) else s.strong_max_start_gap_minutes
            return (
                gap is not None
                and gap <= window * 60
                and matcher.ratio() >= s.strong_title_similarity
                and distance <= s.strong_max_distance_meters
            )
        if a.venue_slug and b.venue_slug:
            return a.venue_slug == b.venue_slug
        # No coordinates or venue on one side: a city is too coarse for "שעת סיפור" at two libraries, so
        # only a near-identical title of 3+ words at the same hour, in the same city or as a promoter
        # site's own copies (GO-OUT's page per promoter often has no place at all).
        s = self.settings
        return (
            (bool(a.city and a.city == b.city) or a.source == b.source and a.source in self._promoter_pages)
            and matcher.ratio() >= s.strong_title_similarity and len(a.title_key.split()) >= 3
            and a.start is not None and b.start is not None
            and abs((a.start - b.start).total_seconds()) <= s.strong_max_start_gap_minutes * 60
        )  # fmt: skip

    def _compatible(self, a: list[_Listing], b: list[_Listing]) -> bool:
        """Matches chain (20:00 ~ 20:50 ~ 21:40): two groups join only if no pair across them contradicts."""
        return not any(self._contradict(x, y) for x in a for y in b)

    def _contradict(self, a: _Listing, b: _Listing, kinds: bool = True) -> bool:
        """Two listings that cannot be one event whatever links or titles say: separate sessions, hours
        apart, other numbers in the title, another city, or another kind of event."""
        s = self.settings
        if self._sessions(a, b):
            return True
        if a.start and b.start and abs((a.start - b.start).total_seconds()) > s.max_start_gap_minutes * 60:
            # A party's listings carry its doors / entry times (20:00 on one site, 21:30 on another); a
            # show's listings hours apart are its sessions.
            gap = abs((a.start - b.start).total_seconds())
            promoter_pages = a.source == b.source and a.source in self._promoter_pages and "music" in a.kinds | b.kinds
            if gap > s.party_max_start_gap_minutes * 60 or not (promoter_pages or self._party(a, b)):
                return True
        if a.numbers and b.numbers and not a.numbers & b.numbers:
            return True
        if a.city and b.city and a.city != b.city:
            distance = _distance_m(a, b)
            if distance is None or distance > s.max_distance_meters:
                return True
        return kinds and self._other_kind(a, b)

    def _party(self, a: _Listing, b: _Listing, ratio: float | None = None) -> bool:
        """Music on two sites under a near-identical title: one party whose sites give other entry times."""
        if a.source == b.source or "music" not in a.kinds | b.kinds or self._other_kind(a, b, 0.0):
            return False
        if ratio is None:
            ratio = SequenceMatcher(None, a.title_key, b.title_key).ratio()
        return ratio >= self.settings.strong_title_similarity

    def _other_kind(self, a: _Listing, b: _Listing, ratio: float | None = None) -> bool:
        """A kids' show and a lecture: genre tags are noisy, so a near-identical title still wins."""
        if not (a.kinds and b.kinds) or a.kinds & b.kinds:
            return False
        if ratio is None:
            ratio = SequenceMatcher(None, a.title_key, b.title_key).ratio()
        return ratio < self.settings.strong_title_similarity

    def _sessions(self, a: _Listing, b: _Listing) -> bool:
        """One site's own pages at different hours (sessions of a workshop / show) or under other titles at
        the same hour ("לידה עד גיל שנה" / "גילאי שנה עד שלוש"): separate events. A promoter site has a page per
        promoter, so only its hours count, and only with the party window (see _contradict)."""
        if a.source != b.source or a.source in self._promoter_pages:
            return False
        return a.title_key != b.title_key or (a.start is not None and b.start is not None and a.start != b.start)

    def _primary_rank(self, item: _Listing) -> tuple[Any, ...]:
        # A sold-out copy never fronts the event while another site still sells it.
        return (item.sold_out, self._priority.get(item.source, len(self._priority)), -item.tiers, item.first_seen, item.id)

    def _group_rows(self, members: list[_Listing]) -> list[dict[str, Any]]:
        """Group id, primary flag and the best deal across all members, written to every member."""
        primary = min(members, key=self._primary_rank)
        # Prices and entry type of what can still be bought; a fully sold-out event keeps its own.
        selling = [m for m in members if not m.sold_out] or members
        priced = [m for m in selling if m.min_price is not None] or [m for m in members if m.min_price is not None]
        best = min(priced, key=lambda m: (m.min_price, self._primary_rank(m))) if priced else primary
        kinds = {k for m in selling for k in _KINDS.get(m.entry_type, ())} or {
            k for m in members for k in _KINDS.get(m.entry_type, ())
        }
        entry_type = (
            "free_and_paid" if kinds == {"free", "paid"} else next(iter(kinds)) if kinds else "unknown"
        )
        maxima = [m.max_price for m in selling if m.max_price is not None] or [
            m.max_price for m in members if m.max_price is not None
        ]
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
        merged, changed = self.plan(session)
        bulk_update_by_id(session, EventRow, changed)
        return merged

    def plan(self, session: Session) -> tuple[int, list[dict[str, Any]]]:
        """(merged groups, rows whose grouping changed): reads only, so it can run outside the write lock."""
        # Same input order, same groups on every pass, on any database: sorted here by code point (the
        # database's own order of text depends on its collation).
        rows = sorted(session.execute(
            select(
                EventRow.id, EventRow.source, EventRow.title, EventRow.local_date, EventRow.start_at,
                EventRow.latitude, EventRow.longitude, EventRow.venue_slug, EventRow.city, EventRow.url, EventRow.extra,
                EventRow.genres, EventRow.first_seen_at, EventRow.min_price, EventRow.max_price, EventRow.entry_type,
                EventRow.has_free_entry, EventRow.free_entry_until, EventRow.is_listed, EventRow.is_sold_out,
            )
        ).all(), key=lambda r: r.id)  # fmt: skip
        tier_counts = _tier_counts(session)
        by_day: dict[Any, list[_Listing]] = defaultdict(list)
        updates: list[dict[str, Any]] = []
        for r in rows:
            extra = r.extra if isinstance(r.extra, dict) else {}
            # Bars' happy hours share a title pattern and a street: only links can join them, never titles.
            title = "" if extra.get(PLACE_LISTING) else title_key(r.title, self._stopwords)
            listing = _Listing(
                id=r.id, source=r.source, title_key=title, start=r.start_at,
                lat=r.latitude, lng=r.longitude, venue_slug=r.venue_slug, city=r.city, url_key=url_key(r.url),
                kinds=frozenset(self._kind[g] for g in r.genres or [] if g in self._kind),
                numbers=frozenset() if extra.get(PLACE_LISTING) else title_numbers(r.title),
                links_to=url_key(_str(extra.get("purchase_url"))) or None,
                tiers=tier_counts.get(r.id, 0), first_seen=r.first_seen_at,
                min_price=r.min_price, max_price=r.max_price, entry_type=r.entry_type,
                has_free_entry=r.has_free_entry, free_entry_until=r.free_entry_until, sold_out=bool(r.is_sold_out),
            )  # fmt: skip
            # An unlisted row stands alone: as a group's primary it would hide its listed copies.
            if r.local_date is None or r.is_listed is False:
                updates += self._group_rows([listing])
            else:
                by_day[r.local_date].append(listing)

        # A page is no one event's page when several listings are that page (a site's generic "central"
        # page), or several listings of one site link to it (a venue's tickets page).
        everyone = [item for day in by_day.values() for item in day]
        keys = Counter(item.url_key for item in everyone if item.url_key)
        linkers = Counter(
            (item.links_to, item.source) for item in everyone if item.links_to and item.source not in self._promoter_pages
        )
        shared = frozenset(k for k, n in keys.items() if n > 1) | frozenset(k for (k, _), n in linkers.items() if n > 1)
        merged = 0
        for listings in by_day.values():
            parent = {item.id: item.id for item in listings}
            members_of = {item.id: [item] for item in listings}

            def find(x: str, parent: dict[str, str] = parent) -> str:
                while parent[x] != x:
                    parent[x] = parent[parent[x]]
                    x = parent[x]
                return x

            # Closest matches first (hour, then distance): a site's 20:00 copy joins the other site's 20:00
            # session, not its 21:30 one.
            pairs = sorted(
                (_closeness(a, b), i, j)
                for i, a in enumerate(listings) for j, b in enumerate(listings[i + 1 :], i + 1)
                if self.same_event(a, b, shared)
            )  # fmt: skip
            for _, i, j in pairs:
                ra, rb = find(listings[i].id), find(listings[j].id)
                if ra != rb and self._compatible(members_of[ra], members_of[rb]):
                    parent[ra] = rb
                    members_of[rb] += members_of.pop(ra)
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
        log.info("dedup: %d listings, %d merged groups, %d rows changed", len(rows), merged, len(changed))
        return merged, changed


def _closeness(a: _Listing, b: _Listing) -> tuple[float, float]:
    gap = abs((a.start - b.start).total_seconds()) if a.start and b.start else 0.0
    distance = _distance_m(a, b)
    return gap, distance if distance is not None else 0.0


def _tier_counts(session: Session) -> dict[str, int]:
    rows = session.execute(select(TicketRow.event_id, func.count()).group_by(TicketRow.event_id))
    return {event_id: count for event_id, count in rows}


def _latest_free_until(members: list[_Listing]) -> datetime | None:
    """Free entry for the event lasts as long as its longest-lasting free listing (None = open-ended)."""
    free = [m.free_entry_until for m in members if m.has_free_entry]
    if not free or any(until is None for until in free):
        return None
    return max(u for u in free if u is not None)
