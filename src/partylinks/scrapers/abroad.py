"""Helpers for sources that sell events abroad: country names to ISO codes, local time zones.

Israeli sellers of shows abroad give the country as a Hebrew or English name (or a code) and the
start as the venue's local time without an offset; these turn both into what ``ScrapedEvent``
needs (ISO country, tz-aware start).

Country names come from ``partylinks.countries`` (one index for the whole app); time zones from airportsdata (every airport's city, country and zone).
"""

from __future__ import annotations

import threading
from collections import Counter, defaultdict
from collections.abc import Callable
from functools import cache, wraps
from typing import Any

import airportsdata

from partylinks.countries import ALIASES, country_code  # noqa: F401 (the sources import them from here)
from partylinks.places import city_zone

_loading = threading.RLock()  # sources run on their own threads: the first caller builds, the rest wait


def _shared[T](build: Callable[[], T]) -> Callable[[], T]:
    """Build once per process and share the result between every source thread."""
    cached = cache(build)

    @wraps(build)
    def get() -> T:
        with _loading:
            return cached()

    get.cache_clear = cached.cache_clear  # type: ignore[attr-defined]
    get.cache_info = cached.cache_info  # type: ignore[attr-defined]
    return get


def _key(value: str) -> str:
    return value.strip().strip(".").casefold()


@_shared
def airports() -> dict[str, Any]:
    """Every airport by IATA code (city, country, tz...), loaded once per process and shared."""
    return airportsdata.load("IATA")


@_shared
def _zones() -> tuple[dict[tuple[str, str], str], dict[str, str]]:
    """(city, country) -> zone, and country -> zone, from the airports' own zones (most common wins)."""
    by_city: defaultdict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    by_country: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for airport in airports().values():
        by_city[_key(airport["city"]), airport["country"]][airport["tz"]] += 1
        by_country[airport["country"]][airport["tz"]] += 1
    return (
        {k: c.most_common(1)[0][0] for k, c in by_city.items()},
        {k: c.most_common(1)[0][0] for k, c in by_country.items()},
    )


def local_zone(country: str | None, default: str, city: str | None = None) -> str:
    """Time zone name of a venue in ``city`` (English or Hebrew name) of ``country`` (ISO), else the
    country's main zone, else ``default``."""
    # ponytail: a country's main zone = its airports' most common one; in multi-zone countries
    # (US, CA, AU, RU) only a known city (GeoNames, 15,000+ people, or an airport's city) is exact.
    by_city, by_country = _zones()
    return (
        (city and country and city_zone(city, country))
        or by_city.get((_key(city or ""), country or ""))
        or by_country.get(country or "", default)
    )
