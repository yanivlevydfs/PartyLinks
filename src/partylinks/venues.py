"""Venue names: fill in the missing ones and merge spellings of the same place.

Every site (and every promoter) writes venues differently - "Gagarin Club TLV",
"מועדון גגרין" - and GO-OUT, ZYGO and Selector often give only a street address.
No alias lists: names are tied together by *where* they are.

* Names whose events sit at the same spot (within ``RADIUS_M``) or at the same
  street + number are one venue, shown under its most common real name (a name
  that is just an address, "שדרות רוטשילד 24", only wins when there is nothing else).
* An event without a name takes the venue at its spot / street + number, or else
  its street address ("Rothschild Blvd 24"). A bare "Tel Aviv" says nothing: its
  coordinates are the city centre, so it stays without a venue.
* A pin shared by several unrelated streets is a site's default location and is ignored.

The scraper's own name is kept in ``extra["venue_source_name"]`` whenever we change
it, so every run recomputes from what the sites said (an upsert restores it anyway).
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from partylinks.db import EventRow, bulk_update_by_id
from partylinks.parsing import slugify

log = logging.getLogger(__name__)

# Geocoders put one place on (almost) one point; neighbouring clubs are ~50 m apart.
RADIUS_M = 25.0
SOURCE_NAME = "venue_source_name"
# extra flag of a listing that is a place's own offer (a bar's happy hour): exact name, exact spot.
PLACE_LISTING = "happy_hour"

# "Kibbutz Galuyot Rd 13" / "הארבעה 28": letters, then one house number, nothing else.
_STREET_NUMBER = re.compile(r"^(?P<street>[^\d,]{2,}?)\s+(?P<number>\d{1,4}[א-תa-z]?)$", re.IGNORECASE)
_STREET_IN_TEXT = re.compile(r"(?P<street>[^\d,]{2,}?)\s+(?P<number>\d{1,4})(?!\d)")
_STREET_WORDS = re.compile(r"\b(?:רחוב|רח'|שדרות|שד'|דרך|st|street|rd|road|blvd|boulevard|ave|avenue)\b\.?", re.IGNORECASE)


@dataclass
class VenueInput:
    id: str
    venue_name: str | None  # what is stored now
    source_name: str | None  # what the site said
    address: str | None
    lat: float | None
    lng: float | None
    city: str | None = None  # canonical city; names only merge within one city


def _distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lng1, lat2, lng2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 6_371_000 * 2 * math.asin(math.sqrt(h))


# Grid cells a little larger than RADIUS_M: points within the radius are in the same or a neighbouring cell.
_CELL_DEG = 0.0003


def _cell(point: tuple[float, float]) -> tuple[int, int]:
    return math.floor(point[0] / _CELL_DEG), math.floor(point[1] / _CELL_DEG)


def _neighbours(cell: tuple[int, int]) -> list[tuple[int, int]]:
    return [(cell[0] + dy, cell[1] + dx) for dy in (-1, 0, 1) for dx in (-1, 0, 1)]


def street_key(address: str | None) -> str | None:
    """"דרך קיבוץ גלויות 52, ..." and "קיבוץ גלויות 52" -> same key."""
    match = _STREET_IN_TEXT.search(address or "")
    if not match:
        return None
    street = slugify(_STREET_WORDS.sub(" ", match["street"]))
    return f"{street}-{match['number']}" if street else None


def street_name(address: str | None) -> str | None:
    """First address segment when it is exactly "street number" - usable as a venue name."""
    first = (address or "").split(",")[0].strip()
    return first if _STREET_NUMBER.match(first) else None


def _pin(r: VenueInput) -> tuple[float, float] | None:
    return None if r.lat is None or r.lng is None else (round(r.lat, 4), round(r.lng, 4))


def _best(votes: Counter[str]) -> str | None:
    """Most common name, preferring real names over ones that are just an address."""
    real = Counter({n: c for n, c in votes.items() if not _STREET_NUMBER.match(n)})
    return (real or votes).most_common(1)[0][0] if votes else None


def infer_venues(rows: list[VenueInput]) -> dict[str, str | None]:
    """The venue name each row should have, for the rows where it differs from now."""
    # A row without a city is in the venue's city when that name is known in one city only.
    cities_of: dict[str, set[str]] = {}
    for r in rows:
        if r.source_name and r.city:
            cities_of.setdefault(r.source_name, set()).add(r.city)
    rows = [
        replace(r, city=next(iter(found)))
        if not r.city and r.source_name and len(found := cities_of.get(r.source_name, set())) == 1
        else r
        for r in rows
    ]
    streets_at: dict[tuple[float, float], set[str]] = {}
    for r in rows:
        if (pin := _pin(r)) and (key := street_key(r.address)):
            streets_at.setdefault(pin, set()).add(key)
    bad_pins = {pin for pin, keys in streets_at.items() if len(keys) > 2}

    def located(r: VenueInput) -> bool:
        return (pin := _pin(r)) is not None and pin not in bad_pins

    # The venue name in its city: "הקאמרי" in Tel Aviv and in Be'er Sheva are two places.
    Node = tuple[str, str]
    city_of = {c: slugify(c) for c in {r.city or "" for r in rows}}
    node = {r.id: (city_of[r.city or ""], r.source_name or "") for r in rows}
    named = [r for r in rows if r.source_name]
    # Each venue once per spot, bucketed by grid cell: neighbours are looked up, not scanned.
    grid: dict[tuple[int, int], dict[tuple[Node, tuple[float, float]], None]] = {}
    for r in named:
        if located(r):
            point = (r.lat, r.lng)
            grid.setdefault(_cell(point), {})[(node[r.id], point)] = None  # type: ignore[index]

    def near(point: tuple[float, float], cities: tuple[str, ...]) -> list[Node]:
        return [
            other
            for cell in _neighbours(_cell(point))
            for other, at in grid.get(cell, {})
            if other[0] in cities and _distance_m(point, at) <= RADIUS_M
        ]

    # Union-find over names in a city: same spot or same street + number -> same venue. Never across
    # cities: a producer's name ("הקאמרי") on its touring shows is not one venue country-wide.
    parent: dict[Node, Node] = {node[r.id]: node[r.id] for r in named}

    def find(n: Node) -> Node:
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    def union(a: Node, b: Node) -> None:
        parent[find(a)] = find(b)

    by_street: dict[tuple[str, str], Node] = {}
    for r in named:
        if key := street_key(r.address):
            union(node[r.id], by_street.setdefault((node[r.id][0], key), node[r.id]))
    for cell in grid.values():
        for here, point in cell:
            for other in near(point, (here[0],)):
                if other != here:
                    union(here, other)

    clusters: dict[Node, Counter[str]] = {}
    for r in named:
        clusters.setdefault(find(node[r.id]), Counter())[r.source_name] += 1  # type: ignore[index]
    canonical = {n: _best(clusters[find(n)]) for n in parent}

    result: dict[str, str | None] = {}
    for row in rows:
        here = node[row.id]
        if row.source_name:
            name = canonical[here]
        elif key := street_key(row.address):
            # The venue in the row's city, or one whose city no source gave.
            close = near((row.lat, row.lng), (here[0], "")) if located(row) else []  # type: ignore[arg-type]
            match = close[0] if close else by_street.get((here[0], key)) or by_street.get(("", key))
            name = canonical[match] if match else street_name(row.address)
        else:
            name = None
        if name != row.venue_name:
            result[row.id] = name
    return result


def resolve_venues(session: Session) -> int:
    """Apply :func:`infer_venues` to the database; returns the number of events changed."""
    db_rows = session.execute(
        select(
            EventRow.id, EventRow.venue_name, EventRow.address, EventRow.latitude, EventRow.longitude, EventRow.extra,
            EventRow.city,
        )
    ).all()
    extras: dict[str, dict[str, Any]] = {r.id: dict(r.extra or {}) for r in db_rows}
    inputs = [
        VenueInput(
            r.id,
            r.venue_name,
            None if extras[r.id].get("venue_inferred") else extras[r.id].get(SOURCE_NAME, r.venue_name),
            r.address,
            r.latitude,
            r.longitude,
            r.city,
        )
        for r in db_rows
        # A bar's own happy hour names its bar exactly: bars 20 m apart on Dizengoff stay apart, and
        # the bar's name never spreads to a club next door.
        if not extras[r.id].get(PLACE_LISTING)
    ]
    sources = {v.id: v.source_name for v in inputs}
    updates = []
    for event_id, name in infer_venues(inputs).items():
        extra = extras[event_id]
        extra.pop("venue_inferred", None)  # marker of the previous version
        if name == sources[event_id]:
            extra.pop(SOURCE_NAME, None)
        else:
            extra[SOURCE_NAME] = sources[event_id]
        updates.append({"id": event_id, "venue_name": name, "venue_slug": slugify(name) or None, "extra": extra})
    bulk_update_by_id(session, EventRow, updates)
    log.info("venues: renamed %d events", len(updates))
    return len(updates)
