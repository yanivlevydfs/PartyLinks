"""Coordinates for events whose source gives only an address, so they can go on the map.

Order per event, first hit wins:
1. a manual pin from ``geocoding.venues`` (venue / address keyword);
2. another stored event at the same venue name or the same street + number;
3. an OpenStreetMap Nominatim answer for the address (cached in ``geo_cache``, hits and misses).

Nominatim answers are only trusted at street / building precision, inside the event's city and,
when the address names a known area of the city, near that area. A city or region centroid
would put a vague "somewhere in the center" party on a fake spot, so those stay off the map.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import timedelta

import httpx
from sqlalchemy import select

from partylinks.config import GeocodingSettings
from partylinks.db import Database, EventRow, GeoCacheRow, upcoming_filter, utcnow
from partylinks.enrichment import AreaMatcher, meters

log = logging.getLogger(__name__)

# Nominatim ``addresstype`` values precise enough for a pin ("place" = a house number).
PRECISE = {
    "place", "house", "building", "road", "amenity", "leisure", "tourism", "shop", "office", "club",
    "historic", "craft", "man_made", "industrial", "commercial",
}  # fmt: skip
_FLOOR = re.compile(r"קומה\s*-?\s*\d*\s*-?|\bfloor\s*-?\d*|\bfl\.?\s*-?\d+", re.IGNORECASE)
_NOT_TEXT = re.compile(r"[^\w\s,.'\"׳״\-/&]", re.UNICODE)  # emojis, symbols
_STREET_WORDS = re.compile(
    r"^\s*(?:דרך|שדרות|שד'|שד׳|רחוב|רח'|רח׳)\s+|\s+(?:st|st\.|street|rd|rd\.|road|blvd|blvd\.|boulevard|ave|ave\.)\b",
    re.IGNORECASE,
)
_STREET_NUMBER = re.compile(r"^((?:[^\W\d_][\w'׳\-]*\s+){1,4}\d{1,4})(?!\d)")  # "Ben Yehuda 102"
_DASHES = re.compile(r"[\s\-–־_]+")

# One request at a time across every source thread (Nominatim usage policy).
_rate_lock = threading.Lock()
_last_request = 0.0


def clean(text: str | None) -> str:
    text = _FLOOR.sub(" ", text or "")
    text = _NOT_TEXT.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip(" ,-")


def street_key(address: str | None) -> str | None:
    """'דרך קיבוץ גלויות 52, תל אביב-יפו' -> 'קיבוץ גלויות 52': street words + house number,
    cut right after the number so 'אבן גבירול 30 תל אביב' and 'אבן גבירול 30, ...' agree."""
    for part in clean(address).split(","):
        part = _STREET_WORDS.sub(" ", f" {part.strip()} ").strip()
        if m := _STREET_NUMBER.match(part):
            return re.sub(r"\s+", " ", m.group(1)).strip().lower()
    return None


_GENERIC_VENUE_WORDS = re.compile(
    r"מועדון|club|bar|lounge|tlv|tel[\s\-]*aviv(?:[\s\-]*yafo)?|תל[\s\-]*אביב(?:[\s\-]*יפו)?|\bthe\b",
    re.IGNORECASE,
)


def venue_key(name: str | None) -> str | None:
    """'מועדון הבארבי' -> 'בארבי', 'JIMMYWHO? Bar & Lounge' -> 'jimmywho': comparable venue names."""
    key = _GENERIC_VENUE_WORDS.sub(" ", (name or "").lower())
    key = re.sub(r"[\W_]+", "", key)
    key = key[1:] if key.startswith("ה") and len(key) > 4 else key  # Hebrew definite article
    return key if len(key) >= 3 else None


def street_variants(address: str | None) -> list[str]:
    """street_key plus shorter forms keeping only the 2 / 1 words before the number, for addresses
    that start with a venue name. Longest first."""
    key = street_key(address)
    if not key:
        return []
    *words, number = key.split()
    return list(dict.fromkeys(" ".join([*words[-n:], number]) for n in (len(words), 2, 1) if n <= len(words)))


def _city_key(text: str | None) -> str:
    return _DASHES.sub("", (text or "").lower())


def candidates(row: EventRow) -> list[str]:
    """Queries to try, most specific first."""
    address, venue, city = clean(row.address), clean(row.venue_name), clean(row.city)
    out: list[str] = []
    if address:
        out.append(f"{address}, {city}" if city and _city_key(city) not in _city_key(address) else address)
        parts = [p.strip() for p in address.split(",")]
        # "CALLAS TLV, Menakhem Begin Rd 37, Tel Aviv" -> drop the leading venue name.
        if len(parts) > 1 and not re.search(r"\d", parts[0]) and any(re.search(r"\d", p) for p in parts[1:]):
            out.append(", ".join(parts[1:]))
        town = city or ("תל אביב" if row.in_target_city else "")
        # "Abarbanel Street 13, ..." -> "abarbanel 13, Tel Aviv"; "מועדון הדאנג'ן הארבעה 10" -> "הארבעה 10, ..."
        for key in street_variants(address):
            out.append(f"{key}, {town}".rstrip(", "))
    if venue:
        out.append(f"{venue}, {city or 'תל אביב'}")
        # "מועדון גגרין" -> "גגרין, תל אביב" (OSM names the place without "club" / "מועדון").
        if (bare := re.sub(r"\s+", " ", _GENERIC_VENUE_WORDS.sub(" ", venue)).strip(" -,")) and len(bare) >= 3:
            out.append(f"{bare}, {city or 'תל אביב'}")
    return list(dict.fromkeys(q for q in out if len(q) >= 4))


def _venue_match(known: dict[str, tuple[float, float]], name: str | None) -> tuple[float, float] | None:
    """Same venue under another spelling: equal keys, or one contains the other (5+ chars)."""
    key = venue_key(name)
    if not key:
        return None
    if key in known:
        return known[key]
    return next((p for k, p in known.items() if min(len(k), len(key)) >= 5 and (k in key or key in k)), None)


@dataclass
class GeoStats:
    missing: int = 0
    located: int = 0
    lookups: int = 0

    def as_dict(self) -> dict[str, int]:
        return {"missing": self.missing, "located": self.located, "lookups": self.lookups}


class Geocoder:
    def __init__(self, settings: GeocodingSettings, areas: AreaMatcher, target_city_aliases: list[str], db: Database) -> None:
        self.settings = settings
        self.areas = areas
        self.db = db
        self._city_keys = {_city_key(a) for a in target_city_aliases if a.strip()}
        self._pins = {k.lower(): v for k, v in settings.venues.items()}

    # ------------------------------------------------------------------ public
    def locate_missing(self, network: bool = True) -> GeoStats:
        """Fill coordinates of upcoming events that have none. Safe to call any time."""
        stats = GeoStats()
        if not self.settings.enabled:
            return stats
        if network:
            stats.lookups = self._lookup_new()
        with self.db.session() as session:
            known_venue, known_street = self._known(session)
            cache = {c.query: c for c in session.scalars(select(GeoCacheRow).where(GeoCacheRow.found.is_(True)))}
            missing = session.scalars(select(EventRow).where(upcoming_filter(), EventRow.latitude.is_(None))).all()
            stats.missing = len(missing)
            for row in missing:
                point = (
                    self._pin(row)
                    or _venue_match(known_venue, row.venue_name)
                    or known_street.get(street_key(row.address) or "")
                    or self._from_cache(row, cache)
                )
                if point:
                    row.latitude, row.longitude = point
                    row.area = self.areas.match(row.venue_name, row.address, *point, row.in_target_city)
                    stats.located += 1
        log.info("geocoding: %s", stats.as_dict())
        return stats

    # ------------------------------------------------------------------ sources of coordinates
    def _pin(self, row: EventRow) -> tuple[float, float] | None:
        text = f"{row.venue_name or ''} | {row.address or ''}".lower()
        return next((tuple(v) for k, v in self._pins.items() if k in text), None)  # type: ignore[return-value]

    @staticmethod
    def _known(session: object) -> tuple[dict[str, tuple[float, float]], dict[str, tuple[float, float]]]:
        rows = session.execute(  # type: ignore[attr-defined]
            select(EventRow.venue_name, EventRow.address, EventRow.latitude, EventRow.longitude).where(
                EventRow.latitude.is_not(None)
            )
        ).all()
        venues = {k: (lat, lon) for v, _, lat, lon in rows if (k := venue_key(v))}
        streets = {k: (lat, lon) for _, a, lat, lon in rows if (k := street_key(a))}
        return venues, streets

    def _from_cache(self, row: EventRow, cache: dict[str, GeoCacheRow]) -> tuple[float, float] | None:
        for query in candidates(row):
            hit = cache.get(query)
            if hit and self._trusted(row, hit):
                return hit.latitude, hit.longitude  # type: ignore[return-value]
        return None

    def _trusted(self, row: EventRow, hit: GeoCacheRow) -> bool:
        if hit.latitude is None or hit.longitude is None or hit.kind not in PRECISE:
            return False
        if row.in_target_city and not any(k in _city_key(hit.city) for k in self._city_keys):
            return False  # e.g. "Kibbutz Galuyot 52" resolved to Bnei Brak
        area = self.areas.keyword_area(row.venue_name, row.address)
        center = self.areas.center(area) if area else None
        return center is None or meters(center, (hit.latitude, hit.longitude)) <= self.settings.max_area_distance_m

    # ------------------------------------------------------------------ network
    def _lookup_new(self) -> int:
        """Ask Nominatim for events still without a place: per event, candidates in order,
        stopping at the first trusted answer. Misses are cached and retried after a while."""
        retry_before = utcnow() - timedelta(days=self.settings.retry_failed_days)
        with self.db.session() as session:
            known_venue, known_street = self._known(session)
            cache = {c.query: c for c in session.scalars(select(GeoCacheRow))}
            pending = [
                row
                for row in session.scalars(select(EventRow).where(upcoming_filter(), EventRow.latitude.is_(None)))
                if not (self._pin(row) or _venue_match(known_venue, row.venue_name) or street_key(row.address) in known_street)
            ]
            session.expunge_all()
        budget = self.settings.max_lookups_per_run
        asked = 0
        with httpx.Client(
            headers={"User-Agent": self.settings.user_agent, "Accept-Language": "he,en"}, timeout=20
        ) as client:
            for row in pending:
                for query in candidates(row):
                    hit = cache.get(query)
                    if hit is None or (not hit.found and hit.looked_up_at < retry_before):
                        if asked >= budget:
                            return asked
                        hit = cache[query] = self._ask(client, query)
                        asked += 1
                        with self.db.session() as session:
                            session.merge(hit)
                    if hit.found and self._trusted(row, hit):
                        break
        return asked

    def _ask(self, client: httpx.Client, query: str) -> GeoCacheRow:
        global _last_request
        with _rate_lock:
            wait = _last_request + self.settings.min_interval_seconds - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            try:
                response = client.get(
                    self.settings.url,
                    params={
                        "q": query, "format": "jsonv2", "limit": self.settings.results_per_query,
                        "addressdetails": 1, "countrycodes": self.settings.country_codes,
                        "viewbox": self.settings.viewbox,
                    },  # fmt: skip
                )
                response.raise_for_status()
                results = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("geocoding %r failed: %s", query, exc)
                results = []
            finally:
                _last_request = time.monotonic()
        for r in results:
            address = r.get("address") or {}
            city = address.get("city") or address.get("town") or address.get("village") or address.get("municipality")
            hit = GeoCacheRow(
                query=query, found=True, latitude=float(r["lat"]), longitude=float(r["lon"]),
                kind=r.get("addresstype"), city=city, label=r.get("display_name"), looked_up_at=utcnow(),
            )  # fmt: skip
            # Same street names exist all over the country / city: take the first hit that fits the query.
            if self._fits(query, hit):
                return hit
        return GeoCacheRow(query=query, found=False, looked_up_at=utcnow())

    def _fits(self, query: str, hit: GeoCacheRow) -> bool:
        if hit.kind not in PRECISE:
            return False
        if any(k in _city_key(query) for k in self._city_keys) and not any(k in _city_key(hit.city) for k in self._city_keys):
            return False
        area = self.areas.keyword_area(None, query)
        center = self.areas.center(area) if area else None
        return center is None or meters(center, (hit.latitude, hit.longitude)) <= self.settings.max_area_distance_m  # type: ignore[arg-type]
