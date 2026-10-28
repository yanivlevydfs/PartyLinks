"""GeoNames cities (``geonamescache``), loaded once and kept small.

geonamescache ships whole-world city files: ``cities500.json`` is 79.5 MB (234,908 cities; ~6 s and
~250 MB to read), ``cities15000.json`` 16.7 MB. The app needs a sliver of them: Israeli localities
(500+ people, ``places.israeli_places``) and cities abroad (the 15,000+ file, ``places._world_cities``),
with only the names it reads (Hebrew, and Latin ones for Israel), centre, population and time zone.

The first process to need them reads both files once and writes that sliver to ``data/`` as one small
JSON (``geonames-<library version>.json``, a few MB); every later start reads only that file. A new
geonamescache version gets a new file, so an upgrade rebuilds it. Nothing here is hand-written data:
the file is the library's own records, filtered.
"""

from __future__ import annotations

import importlib.metadata
import json
import logging
import re
import threading
from functools import cache
from pathlib import Path
from typing import Any

import geonamescache

from partylinks.config import project_path

log = logging.getLogger(__name__)

_HEBREW = re.compile(r"[֐-׿]")
ISRAEL_MIN, WORLD_MIN = 500, 15000  # the geonamescache files read: cities500 (Israel), cities15000 (abroad)
_lock = threading.Lock()  # sources run on their own threads: the first caller builds, the rest wait


def cache_file() -> Path:
    return project_path("data") / f"geonames-{importlib.metadata.version('geonamescache')}.json"


def _keep(city: dict[str, Any], latin: bool) -> dict[str, Any]:
    """A city with only the fields and names the app reads (Hebrew names; Latin ones for Israel)."""
    names = [a for a in city["alternatenames"] if _HEBREW.search(a) or (latin and a.isascii() and len(a) > 2)]
    return {k: city[k] for k in ("geonameid", "name", "countrycode", "population", "latitude", "longitude", "timezone")} | {
        "alternatenames": names
    }


def build() -> dict[str, list[dict[str, Any]]]:
    """Read the library's files (the slow part, once per library version)."""
    israel = geonamescache.GeonamesCache(min_city_population=ISRAEL_MIN).get_cities().values()
    world = geonamescache.GeonamesCache(min_city_population=WORLD_MIN).get_cities().values()
    return {
        "israel": [_keep(c, latin=True) for c in israel if c["countrycode"] == "IL"],
        "world": [_keep(c, latin=False) for c in world if c["countrycode"] != "IL"],
    }


@cache
def _cities() -> dict[str, list[dict[str, Any]]]:
    path = cache_file()
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        pass  # first start for this library version, or an unreadable file: rebuild it
    data = build()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
        tmp.replace(path)
        log.info("geonames: cached %d Israeli and %d world cities in %s", len(data["israel"]), len(data["world"]), path)
    except OSError as exc:  # a read-only disk: works, just reads the library again next start
        log.warning("geonames: cache not written (%s)", exc)
    return data


def israel() -> list[dict[str, Any]]:
    """Israeli localities of 500+ people (GeoNames records, trimmed)."""
    with _lock:
        return _cities()["israel"]


def world() -> list[dict[str, Any]]:
    """Cities abroad of 15,000+ people (and capitals), GeoNames records trimmed to Hebrew names."""
    with _lock:
        return _cities()["world"]
