"""Coordinates for events whose source gives only an address, so they can go on the map.

Order per event, first hit wins:
1. a manual pin from ``geocoding.venues`` (venue / address keyword);
2. another stored event at the same venue name or the same street + number;
3. an OpenStreetMap Nominatim answer for the address (cached in ``geo_cache``, hits and misses).

Nominatim answers are only trusted at street / building precision, inside the event's city and,
when the address names a known area of the city, near that area. A city or region centroid
would put a vague "somewhere in the center" party on a fake spot, so those stay off the map.
Events abroad (not on the map) take their city's point instead, for the weather forecast.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import select

from partylinks.config import CitySettings, GeocodingSettings
from partylinks.db import Database, EventRow, GeoCacheRow, bulk_update_by_id, upcoming_filter, utcnow, with_db_retry
from partylinks.enrichment import UNKNOWN_COUNTRY, AreaMatcher, meters

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

# One point given for this many different venues is a source's fallback ("somewhere in Tel Aviv"), not a place.
PLACEHOLDER_VENUES = 4

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


def _point(row: Place) -> tuple[float, float]:
    return round(row.latitude or 0, 5), round(row.longitude or 0, 5)


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


def candidates(row: Place) -> list[str]:
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
        # Unknown city: assume Tel Aviv unless the event is known to be elsewhere.
        town = city or ("" if row.in_target_city is False else "תל אביב")
        out += [f"{v}, {town}".rstrip(", ") for v in venue_variants(venue)]
        # "מועדון גגרין" -> "גגרין, תל אביב" (OSM names the place without "club" / "מועדון").
        if (bare := re.sub(r"\s+", " ", _GENERIC_VENUE_WORDS.sub(" ", venue)).strip(" -,")) and len(bare) >= 3:
            out.append(f"{bare}, {town}".rstrip(", "))
    return list(dict.fromkeys(q for q in out if len(q) >= 4))


# A name made only of one of these would match any hall / theatre / bar in town.
_GENERIC_NAMES = {
    "אולם", "תיאטרון", "מוזיאון", "מרכז", "מועדון", "בר", "היכל", "סטודיו", "אודיטוריום", "קלאב", "גלריה",
    "פאב", "חדר", "במה", "club", "bar", "hall", "center", "centre", "studio", "theater", "theatre", "the",
}  # fmt: skip


_TRAILING_CITY = re.compile(r"[\s,\-]*(?:תל[\s\-]*אביב(?:[\s\-]*יפו)?|ת\"א(?:[\s\-]*יפו)?|ת״א|tel[\s\-]*aviv(?:[\s\-]*yafo)?)$", re.IGNORECASE)


def venue_variants(venue: str) -> list[str]:
    """'מוזיאון תל אביב, אולם אסיא' -> ['מוזיאון תל אביב, אולם אסיא', 'מוזיאון תל אביב', 'מוזיאון תל', ...]:
    OSM names places shorter than ticket sites do ("הוואנה מיוזיק קלאב" is "האוונה קלאב").
    Every hit is still checked for precision and city before it is used."""
    first = re.split(r"\s*[,|–]\s*|\s+-\s+", venue)[0].strip()
    no_city = _TRAILING_CITY.sub("", first).strip()  # "גריי תל אביב" -> "גריי"
    words = no_city.split()
    shorter = [" ".join(words[:n]) for n in range(len(words) - 1, 0, -1)]
    shorter = [v for v in shorter if v.split()[-1].lower() not in {"תל", "tel"}]  # never cut inside "תל אביב"
    keep = [
        v for v in [venue, first, no_city, *shorter]
        if v.lower() not in _GENERIC_NAMES and (len(v.split()) > 1 or len(v) >= 5)
    ]  # fmt: skip
    return list(dict.fromkeys(keep))


Point = tuple[float, float]


# What placing an event reads: plain rows, not ORM objects held in a write session.
_PLACE_COLUMNS = (
    EventRow.id, EventRow.venue_name, EventRow.address, EventRow.city, EventRow.in_target_city,
    EventRow.latitude, EventRow.longitude,
)  # fmt: skip
# An EventRow or a plain row of _PLACE_COLUMNS: same attribute names; the type checkers model a
# SQLAlchemy Row as a bare tuple, so this stays untyped.
Place = Any
# Known places by key, per city ("" = no city given): "הקאמרי" in Tel Aviv and in Be'er Sheva differ.
Known = dict[str, dict[str, Point]]
# Partial name matches need this many letters ("קאמרי" in "תיאטרוןהקאמרי"); always within one city.
MIN_PARTIAL = 5


def _in_city(by_city: dict[str, Point] | None, city: str | None) -> Point | None:
    """The place in the event's city; with no city, only a place known in one city."""
    if not by_city:
        return None
    if city:
        return by_city.get(city) or by_city.get("")
    return next(iter(by_city.values())) if len(by_city) == 1 else None


def _venue_match(known: Known, name: str | None, city: str | None = None) -> Point | None:
    """Same venue under another spelling, in the same city: equal keys, or one contains the other."""
    key = venue_key(name)
    if not key:
        return None
    if point := _in_city(known.get(key), city):
        return point
    return next(
        (p for k, cities in known.items()
         if min(len(k), len(key)) >= MIN_PARTIAL and (k in key or key in k) and (p := _in_city(cities, city))),
        None,
    )  # fmt: skip


@dataclass
class GeoStats:
    missing: int = 0  # upcoming events without coordinates before this pass
    located: int = 0  # ... placed by this pass
    lookups: int = 0  # Nominatim requests made
    not_found: int = 0  # still unplaced: every query was tried and missed (retried after retry_failed_days)
    no_place: int = 0  # still unplaced: no venue name or address to look up
    unplaced_venues: list[str] = field(default_factory=list)  # most frequent, worth a manual pin

    def as_dict(self) -> dict[str, int]:
        return {"missing": self.missing, "located": self.located, "lookups": self.lookups,
                "not_found": self.not_found, "no_place": self.no_place}  # fmt: skip

    def summary(self) -> str:
        if not self.missing:
            return "every upcoming event has coordinates"
        text = (f"{self.missing} upcoming events without coordinates: {self.located} placed now, "
                f"{self.lookups} new lookups; still unplaced: {self.not_found} not found earlier (cached), "
                f"{self.no_place} without venue/address")  # fmt: skip
        if self.unplaced_venues:
            text += "; pin in geocoding.venues: " + ", ".join(f'"{v}"' for v in self.unplaced_venues)
        return text


class Geocoder:
    def __init__(
        self, settings: GeocodingSettings, areas: AreaMatcher, target_city_aliases: list[str], db: Database,
        cities: list[CitySettings] | None = None,
    ) -> None:
        self.settings = settings
        self._cities = {c.he: c for c in cities or [] if c.center}
        self.areas = areas
        self.db = db
        self._city_keys = {_city_key(a) for a in target_city_aliases if a.strip()}
        self._pins = {k.lower(): v for k, v in settings.venues.items()}

    # ------------------------------------------------------------------ public
    def locate_missing(self, network: bool = True, stop: threading.Event | None = None) -> GeoStats:
        """Fill coordinates of upcoming events that have none. Safe to call any time."""
        stats = GeoStats()
        if not self.settings.enabled:
            return stats
        self.drop_bad_points()
        if network:
            stats.lookups = self._lookup_new(stop)
        stats.located += self._locate_abroad(network, stop)
        # Matched outside the write lock (after a full scrape ~18k rows, minutes); written in one short session.
        with self.db.session(write=False) as session:
            known_venue, known_street = self._known(session)
            cache = {c.query: c for c in session.scalars(select(GeoCacheRow).where(GeoCacheRow.found.is_(True)))}
            missing = session.execute(select(*_PLACE_COLUMNS).where(upcoming_filter(), EventRow.latitude.is_(None), EventRow.country.is_(None))).all()
        stats.missing = len(missing)
        unplaced: Counter[str] = Counter()
        updates = []
        for row in missing:
            point = next(
                (p for p in (
                    self._pin(row),
                    _venue_match(known_venue, row.venue_name, row.city),
                    _in_city(known_street.get(street_key(row.address) or ""), row.city),
                    self._from_cache(row, cache),
                ) if p and self._in_its_city(row, p)),
                None,
            )  # fmt: skip
            if point:
                area = self.areas.match(row.venue_name, row.address, *point, row.in_target_city, row.city)
                updates.append({"id": row.id, "latitude": point[0], "longitude": point[1], "area": area})
            elif candidates(row):
                stats.not_found += 1
                unplaced[row.venue_name or row.address or ""] += 1
            else:
                stats.no_place += 1
        if updates:
            # ponytail: an event a scrape re-saved meanwhile gets this point back; the next pass re-checks it.
            with self.db.session() as session:
                bulk_update_by_id(session, EventRow, updates)
        stats.located += len(updates)
        stats.unplaced_venues = [v for v, _ in unplaced.most_common(5) if v]
        log.info("geocoding: %s", stats.summary())
        return stats

    def drop_bad_points(self) -> int:
        """Source coordinates are not always real: Go-Out sends the centre of Israel (in the Negev)
        or one fixed Tel Aviv point when it has no location. Clear points far outside the event's
        city and points shared by several different venues; the pass then places those events
        from their venue or address like any other. ponytail: runs after every scrape, so a source
        that re-sends a bad point shows it on the map until that source's geocode step."""
        with self.db.session(write=False) as session:  # read and judge outside the write lock
            # Abroad points are city centres shared by every venue there (_locate_abroad): not placeholders.
            rows = session.execute(select(*_PLACE_COLUMNS).where(EventRow.latitude.is_not(None), EventRow.country.is_(None))).all()
        venues: defaultdict[tuple[float, float], set[str]] = defaultdict(set)
        for r in rows:
            venues[_point(r)].add(venue_key(r.venue_name) or street_key(r.address) or "")
        bad = []
        for r in rows:
            far = not self._in_its_city(r, (r.latitude, r.longitude))  # type: ignore[arg-type]
            # Outside the target city the place in the text wins: "קיבוץ הגושרים" is in the Galilee.
            far = far or (
                r.in_target_city is False
                and self.areas.contradicts(r.city, r.venue_name, r.address, r.latitude, r.longitude)  # type: ignore[arg-type]
            )
            if far or len(venues[_point(r)]) >= PLACEHOLDER_VENUES:
                bad.append({"id": r.id, "latitude": None, "longitude": None})
        if bad:
            with self.db.session() as session:
                bulk_update_by_id(session, EventRow, bad)
        dropped = len(bad)
        if dropped:
            log.info("geocoding: dropped %d source coordinates that are placeholders or outside the city", dropped)
        return dropped

    def _in_its_city(self, row: Place, point: Point) -> bool:
        """Within the radius of the event's city, when that city is a known one."""
        city = self._cities.get(row.city or "")
        return city is None or meters(city.center, point) <= city.radius_m  # type: ignore[arg-type]

    # ------------------------------------------------------------------ sources of coordinates
    def _pin(self, row: Place) -> tuple[float, float] | None:
        text = f"{row.venue_name or ''} | {row.address or ''}".lower()
        return next((tuple(v) for k, v in self._pins.items() if k in text), None)  # type: ignore[return-value]

    def _known(self, session: object) -> tuple[Known, Known]:
        """Placed venues and streets per city; a point outside its own city teaches nothing."""
        rows = session.execute(  # type: ignore[attr-defined]
            select(EventRow.venue_name, EventRow.address, EventRow.city, EventRow.latitude, EventRow.longitude).where(
                EventRow.latitude.is_not(None)
            )
        ).all()
        venues: Known = {}
        streets: Known = {}
        for row in rows:
            name, address, city, lat, lon = row
            if self._in_its_city(row, (lat, lon)):
                if key := venue_key(name):
                    venues.setdefault(key, {})[city or ""] = (lat, lon)
                if key := street_key(address):
                    streets.setdefault(key, {})[city or ""] = (lat, lon)
        return venues, streets

    def _from_cache(self, row: Place, cache: dict[str, GeoCacheRow]) -> tuple[float, float] | None:
        for query in candidates(row):
            hit = cache.get(query)
            if hit and self._trusted(row, hit):
                return hit.latitude, hit.longitude  # type: ignore[return-value]
        return None

    def _trusted(self, row: Place, hit: GeoCacheRow) -> bool:
        if hit.latitude is None or hit.longitude is None or hit.kind not in PRECISE:
            return False
        if not self._in_its_city(row, (hit.latitude, hit.longitude)):
            return False  # "Gray Club, יהוד" answered with the Gray Club in Modiin
        if row.in_target_city and not any(k in _city_key(hit.city) for k in self._city_keys):
            return False  # e.g. "Kibbutz Galuyot 52" resolved to Bnei Brak
        if row.in_target_city is False and self.areas.contradicts(
            row.city, row.venue_name, row.address, hit.latitude, hit.longitude
        ):
            return False
        # Area keywords are Tel Aviv streets ("הרצל", "בן יהודה" exist in every town).
        area = self.areas.keyword_area(row.venue_name, row.address) if row.in_target_city is not False else None
        center = self.areas.center(area) if area else None
        return center is None or meters(center, (hit.latitude, hit.longitude)) <= self.settings.max_area_distance_m

    # ------------------------------------------------------------------ network
    def _lookup_new(self, stop: threading.Event | None = None) -> int:
        """Ask Nominatim for events still without a place: per event, candidates in order,
        stopping at the first trusted answer. Misses are cached and retried after a while."""
        retry_before = utcnow() - timedelta(days=self.settings.retry_failed_days)
        with self.db.session() as session:
            known_venue, known_street = self._known(session)
            cache = {c.query: c for c in session.scalars(select(GeoCacheRow))}
            pending = [
                row
                for row in session.scalars(select(EventRow).where(upcoming_filter(), EventRow.latitude.is_(None), EventRow.country.is_(None)))
                if not (
                    self._pin(row)
                    or _venue_match(known_venue, row.venue_name, row.city)
                    or _in_city(known_street.get(street_key(row.address) or ""), row.city)
                )
            ]
            session.expunge_all()
        budget = self.settings.max_lookups_per_run
        asked = 0
        with httpx.Client(
            headers={"User-Agent": self.settings.user_agent, "Accept-Language": "he,en"}, timeout=20
        ) as client:
            for row in pending:
                if stop is not None and stop.is_set():
                    return asked  # shutting down: don't hold the process for minutes of 1 req/s lookups
                for query in candidates(row):
                    hit = cache.get(query)
                    if hit is None or (not hit.found and hit.looked_up_at < retry_before):
                        if asked >= budget:
                            return asked
                        hit = cache[query] = self._ask(client, query)
                        asked += 1
                        with_db_retry("geo cache", lambda hit=hit: self._remember(hit))  # sources save in parallel
                    if hit.found and self._trusted(row, hit):
                        break
        return asked

    def _locate_abroad(self, network: bool, stop: threading.Event | None) -> int:
        """Events abroad: their city's point, enough for the weather (abroad is not on the map).
        One lookup per city, in the same cache, rate limit and budget as the lookups in Israel."""
        unplaced = (upcoming_filter(), EventRow.latitude.is_(None), EventRow.country.is_not(None), EventRow.city.is_not(None))

        def key(city: str, country: str) -> str:
            return f"{clean(city)} @{country}"

        with self.db.session(write=False) as session:
            places = session.execute(select(EventRow.city, EventRow.country).where(*unplaced).distinct()).all()
            cache = {c.query: c for c in session.scalars(select(GeoCacheRow).where(GeoCacheRow.query.like("% @__")))}
        if network:
            retry_before = utcnow() - timedelta(days=self.settings.retry_failed_days)
            todo = list(dict.fromkeys(
                (city, country) for city, country in places
                if clean(city) and ((hit := cache.get(key(city, country))) is None or (not hit.found and hit.looked_up_at < retry_before))
            ))[: self.settings.max_lookups_per_run]  # fmt: skip
            if todo:
                with httpx.Client(
                    headers={"User-Agent": self.settings.user_agent, "Accept-Language": "he,en"}, timeout=20
                ) as client:
                    for city, country in todo:
                        if stop is not None and stop.is_set():
                            break
                        hit = self._ask(client, clean(city), country)
                        hit.query = key(city, country)
                        cache[hit.query] = hit
                        with_db_retry("geo cache", lambda hit=hit: self._remember(hit))
        with self.db.session(write=False) as session:
            rows = session.execute(select(EventRow.id, EventRow.city, EventRow.country).where(*unplaced)).all()
        updates = [
            {"id": r.id, "latitude": hit.latitude, "longitude": hit.longitude}
            for r in rows if (hit := cache.get(key(r.city or "", r.country or ""))) and hit.found
        ]  # fmt: skip
        if updates:
            with self.db.session() as session:
                bulk_update_by_id(session, EventRow, updates)
        return len(updates)

    def _remember(self, hit: GeoCacheRow) -> None:
        with self.db.session() as session:
            session.merge(hit)

    def _ask(self, client: httpx.Client, query: str, country: str | None = None) -> GeoCacheRow:
        """With ``country`` (abroad): a city anywhere in that country, any precision."""
        global _last_request
        with _rate_lock:
            wait = _last_request + self.settings.min_interval_seconds - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            try:
                params = {
                    "q": query, "format": "jsonv2", "limit": self.settings.results_per_query,
                    "addressdetails": 1, "countrycodes": self.settings.country_codes, "viewbox": self.settings.viewbox,
                }  # fmt: skip
                if country:
                    params.pop("viewbox")
                    if country == UNKNOWN_COUNTRY:
                        params.pop("countrycodes")
                    else:
                        params["countrycodes"] = country.lower()
                response = client.get(self.settings.url, params=params)
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
            if country or self._fits(query, hit):
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
