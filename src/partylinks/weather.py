"""Weather forecast for events: ``GET /weather?lat=..&lng=..``.

Source: MET Norway's Locationforecast (api.met.no), free for any use with an identifying
User-Agent: hourly for the next ~2.5 days, then every 6 hours up to ~10 days. Positions are
rounded to 0.1° (~10 km), so every event of a city shares one forecast. A forecast is kept at
least an hour (longer if MET's ``Expires`` says so; MET updates about hourly), one fetch per
place at a time, and saved to ``weather_cache.json`` beside the database so a deploy or restart
never refetches. Browsers keep it 15 minutes on top. The site picks each event's start slot.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, Response

from partylinks.config import Settings, project_path
from partylinks.places import PlaceIndex, all_cities

log = logging.getLogger(__name__)

MET_URL = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
MIN_TTL = 3600.0  # never refetch a place sooner, whatever MET's Expires says
FAILURE_TTL = 300.0  # a failed fetch is not retried for every card on the page
# Whatever callers ask (the endpoint is public), at most this many MET fetches per window.
MAX_FETCHES, FETCH_WINDOW = 120, 600.0

router = APIRouter(tags=["weather"])
# (lat, lng) cell -> (expires at, slots); one fetch per cell at a time.
_cache: dict[tuple[float, float], tuple[float, list[list[Any]]]] = {}
_locks: dict[tuple[float, float], asyncio.Lock] = {}
_loaded_from: Path | None = None
_fetched_at: list[float] = []


def _may_fetch() -> bool:
    now = time.time()
    _fetched_at[:] = [t for t in _fetched_at if t > now - FETCH_WINDOW]
    if len(_fetched_at) >= MAX_FETCHES:
        return False
    _fetched_at.append(now)
    return True


def cache_file(settings: Settings) -> Path:
    """On the data volume (data/, Railway's /app/data)."""
    return project_path("data") / "weather_cache.json"


def _load(path: Path) -> None:
    global _loaded_from
    if _loaded_from == path:
        return
    _loaded_from = path
    try:
        rows = json.loads(path.read_text("utf-8"))
        now = time.time()
        _cache.update({(lat, lng): (expires, slots) for lat, lng, expires, slots in rows if expires > now})
    except (OSError, ValueError, TypeError) as exc:
        if path.exists():
            log.warning("weather cache %s unreadable: %s", path, exc)


def _save(path: Path) -> None:
    rows = [[lat, lng, expires, slots] for (lat, lng), (expires, slots) in _cache.items() if slots]
    tmp = path.with_suffix(".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(rows, separators=(",", ":")), "utf-8")
        tmp.replace(path)
    except OSError as exc:
        log.warning("weather cache %s not saved: %s", path, exc)


def parse_forecast(payload: dict[str, Any]) -> list[list[Any]]:
    """MET timeseries -> [[time, symbol, °C, mm, hours], ...]: the sky over the next `hours` from `time`."""
    slots: list[list[Any]] = []
    for step in (payload.get("properties") or {}).get("timeseries") or []:
        data = step.get("data") or {}
        for hours, key in ((1, "next_1_hours"), (6, "next_6_hours")):
            period = data.get(key)
            if period and (symbol := (period.get("summary") or {}).get("symbol_code")):
                temp = ((data.get("instant") or {}).get("details") or {}).get("air_temperature")
                rain = (period.get("details") or {}).get("precipitation_amount")
                slots.append([step.get("time"), symbol, temp, rain, hours])
                break
    return slots


async def forecast(lat: float, lng: float, user_agent: str, path: Path) -> list[list[Any]]:
    _load(path)
    cell = (round(lat, 1), round(lng, 1))
    async with _locks.setdefault(cell, asyncio.Lock()):
        cached = _cache.get(cell)
        if cached and cached[0] > time.time():
            return cached[1]
        if not _may_fetch():
            log.warning("weather: %d MET fetches in %.0f min, %s waits", MAX_FETCHES, FETCH_WINDOW / 60, cell)
            return cached[1] if cached else []
        try:
            async with httpx.AsyncClient(timeout=15, headers={"User-Agent": user_agent}) as client:
                response = await client.get(MET_URL, params={"lat": cell[0], "lon": cell[1]})
                response.raise_for_status()
            slots, ttl = parse_forecast(response.json()), _ttl(response.headers.get("expires"))
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("weather for %s failed: %s", cell, exc)
            slots, ttl = (cached[1] if cached else []), FAILURE_TTL
        _cache[cell] = (time.time() + ttl, slots)
        _save(path)
        return slots


def _ttl(expires: str | None) -> float:
    try:
        return max(MIN_TTL, parsedate_to_datetime(expires).timestamp() - time.time()) if expires else MIN_TTL
    except (TypeError, ValueError):
        return MIN_TTL


_places: tuple[PlaceIndex, dict[str, tuple[float, float]]] | None = None


def _city_center(settings: Settings, name: str) -> tuple[float, float] | None:
    """A city name, in Hebrew or English, with or without its suffix ("תל אביב-יפו") -> its centre."""
    global _places
    if _places is None:
        cities = all_cities(settings.enrichment.cities, settings.enrichment.places_min_population)
        _places = PlaceIndex(cities), {c.he: c.center for c in cities if c.center}
    index, centers = _places
    return next((centers[he] for _, he in index.find(name) if he in centers), None)


# Places no list has (regional councils "חוף השרון", small towns): one Nominatim lookup per name ever,
# kept beside the forecast cache (misses too, so a typo is not asked again).
_found: dict[str, list[float] | None] = {}
_found_from: Path | None = None
_nominatim_lock = asyncio.Lock()
# A settlement or a council's region, never a street or a building of the same name.
_PLACE_TYPES = {"city", "town", "village", "hamlet", "suburb", "municipality", "region", "county", "state_district"}


async def _looked_up(settings: Settings, name: str, path: Path) -> tuple[float, float] | None:
    global _found_from
    if _found_from != path:
        _found_from = path
        try:
            _found.update(json.loads(path.read_text("utf-8")))
        except (OSError, ValueError):
            pass
    key = name.strip()
    if key not in _found:
        async with _nominatim_lock:  # Nominatim allows 1 request a second
            if key not in _found and _may_fetch():
                geo = settings.geocoding
                try:
                    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": geo.user_agent}) as client:
                        # Same country codes as the geocoder: with "il" alone, Israeli localities that OSM files
                        # under "ps" (Oranit, Ma'ale Adumim...) were missed and a same-named street won.
                        hits = (await client.get(geo.url, params={"q": key, "format": "jsonv2", "limit": 5, "countrycodes": geo.country_codes})).json()
                    place = next((h for h in hits if h.get("addresstype") in _PLACE_TYPES), None)
                    _found[key] = [float(place["lat"]), float(place["lon"])] if place else None
                    tmp = path.with_suffix(".tmp")
                    tmp.write_text(json.dumps(_found, ensure_ascii=False), "utf-8")
                    tmp.replace(path)
                except (httpx.HTTPError, ValueError, KeyError, OSError) as exc:
                    log.warning("weather place %r not looked up: %s", key, exc)
                await asyncio.sleep(1)
    point = _found.get(key)
    return (point[0], point[1]) if point else None


@router.get("/weather", summary="Forecast slots for a place (events pick their start time's slot)")
async def weather(
    request: Request,
    response: Response,
    lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
    lng: Annotated[float | None, Query(ge=-180, le=180)] = None,
    city: Annotated[str | None, Query(max_length=60, description="A configured city's Hebrew name: its centre")] = None,
) -> dict[str, list[list[Any]]]:
    settings: Settings = request.app.state.settings
    # An event placed only by its city (no exact point) takes the city centre's forecast: the configured
    # cities, then every GeoNames town (ערד, כפר ויתקין, מעלה אדומים... were 404s with the first alone).
    if lat is None or lng is None:
        # "קיבוץ יגור" / "מושב בית יצחק": the lists and Nominatim know the bare name.
        name = re.sub(r"^(קיבוץ|מושב|מושבה)\s+", "",(city or "").strip()) if city else ""
        center = _city_center(settings, city or "") or (name != city and _city_center(settings, name) or None)
        if center is None and name:
            center = await _looked_up(settings, name, cache_file(settings).with_name("weather_places.json"))
        if center is None:
            # Empty, not 404: an unknown place just shows no forecast, without a red line in every console.
            response.headers["Cache-Control"] = "public, max-age=86400"
            return {"slots": []}
        lat, lng = center
    slots = await forecast(lat, lng, f"SPOTX/1.0 (+{settings.site.base_url})", cache_file(settings))
    if not slots:
        raise HTTPException(status_code=503, detail="forecast unavailable")
    response.headers["Cache-Control"] = "public, max-age=900"
    return {"slots": slots}
