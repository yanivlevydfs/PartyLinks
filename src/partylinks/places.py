"""Every Israeli locality from GeoNames (``geonamescache``, offline), loaded once per process.

``enrichment.cities`` in the config names the cities that get their own page and spelling; this
fills in the rest (Yehud, Shoham, Rosh Pina, ...) with a canonical Hebrew name, a centre and a
radius, so an address in any town gets its city and a map point outside that town is rejected.

Names are found by word lookups in a dict, not one regex per town: an address costs a few dict
probes whatever the number of towns (28k events x ~200 towns of regexes took seconds).
"""

from __future__ import annotations

import math
import re
from functools import lru_cache

import geonamescache

from partylinks.config import CitySettings
from partylinks.parsing import slugify

_HEBREW = re.compile(r"[֐-׿]")
_NIQQUD = re.compile(r"[֑-ׇ]")  # vowel and cantillation marks: "סַבְיוֹן" -> "סביון"
_WORD = re.compile(r"[\w'׳\"״]+")
_PREFIXES = "ובהלמשכ"
MAX_WORDS = 4  # "קריית שמונה", "מודיעין מכבים רעות"


def _norm(text: str) -> str:
    return " ".join(_WORD.findall(text.lower().replace("־", " ").replace("-", " ").replace("–", " ")))


def _radius_m(population: int) -> float:
    """~9 km for Tel Aviv, ~3 km for Yehud, ~1.5 km for a small town."""
    return 1000 + 12 * math.sqrt(population)


@lru_cache(maxsize=4)
def israeli_places(min_population: int) -> tuple[CitySettings, ...]:
    """Localities of at least ``min_population`` (smaller ones are often words: "מגדל", "כנרת")."""
    places = []
    for c in geonamescache.GeonamesCache(min_city_population=500).get_cities().values():
        if c["countrycode"] != "IL" or c["population"] < min_population:
            continue
        hebrew = list(dict.fromkeys(_NIQQUD.sub("", a) for a in c["alternatenames"] if _HEBREW.search(a)))
        if not hebrew:
            continue
        latin = [a for a in c["alternatenames"] if a.isascii() and len(a) > 2]
        places.append(
            CitySettings(
                key=slugify(c["name"]) or str(c["geonameid"]),
                he=hebrew[0],
                en=c["name"],
                aliases=[*hebrew[1:], *latin],
                center=(c["latitude"], c["longitude"]),
                radius_m=_radius_m(c["population"]),
            )
        )
    return tuple(sorted(places, key=lambda p: p.radius_m, reverse=True))  # bigger town first on a name clash


class PlaceIndex:
    """Phrase -> canonical city, for the configured cities and the GeoNames towns."""

    def __init__(self, cities: list[CitySettings]) -> None:
        self._names: dict[str, str] = {}
        for city in cities:  # first one wins: configured cities come first
            for name in (city.he, city.en, *city.aliases):
                if key := _norm(name):
                    self._names.setdefault(key, city.he)

    def find(self, text: str | None) -> list[tuple[int, str]]:
        """(word position, city) of every place named in ``text``; a Hebrew prefix letter is allowed
        ("ביהוד", "מתל אביב")."""
        words = _norm(text or "").split()
        hits = []
        for i in range(len(words)):
            for n in range(min(MAX_WORDS, len(words) - i), 0, -1):
                phrase = " ".join(words[i : i + n])
                city = self._names.get(phrase)
                if city is None and phrase[0] in _PREFIXES and len(phrase) > 3:
                    city = self._names.get(phrase[1:])
                if city is not None:
                    hits.append((i, city))
                    break
        return hits


def all_cities(configured: list[CitySettings], min_population: int) -> list[CitySettings]:
    """The configured cities, then every GeoNames town none of them already names."""
    known = PlaceIndex(configured)
    extra = [p for p in israeli_places(min_population) if not any(known.find(n) for n in (p.he, p.en))]
    return [*configured, *extra]


@lru_cache(maxsize=1)
def _world_cities() -> dict[str, list[tuple[int, str, int, tuple[str, ...], str]]]:
    """Every city abroad of 15,000+ people by each of its names (English, Hebrew), normalised:
    [(GeoNames id, country, population, its names, its time zone)]. Loaded once per process."""
    index: dict[str, list[tuple[int, str, int, tuple[str, ...], str]]] = {}
    for c in geonamescache.GeonamesCache(min_city_population=15000).get_cities().values():
        if c["countrycode"] == "IL":
            continue
        hebrew = [_NIQQUD.sub("", a) for a in c["alternatenames"] if _HEBREW.search(a)]
        names = tuple(dict.fromkeys([c["name"], *hebrew]))
        for name in names:
            index.setdefault(_norm(name), []).append(
                (int(c["geonameid"]), c["countrycode"], c["population"], names, c["timezone"])
            )
    return index


_PARTS = re.compile(r"[,()/|]")


def _city(name: str, country: str | None, min_population: int) -> tuple[int, str, int, tuple[str, ...], str] | None:
    """The city called ``name``: the one in ``country`` when given, else the largest of that name. A name
    with extras ("לונדון, אנגליה", "Saint-Denis (Paris)") is the largest city it names."""
    cities = _world_cities()
    found = [c for c in cities.get(_norm(name), ()) if c[2] >= min_population]
    if not found:
        parts = _PARTS.split(name)
        found = [c for p in parts for c in cities.get(_norm(p), ()) if len(parts) > 1 and c[2] >= min_population]
        if country:
            found = [c for c in found if c[1] == country]
    return max(found, key=lambda c: (c[1] == country, c[2])) if found else None


def city_abroad(name: str, country: str | None = None, min_population: int = 0) -> tuple[int, tuple[str, ...]] | None:
    """The city abroad called ``name`` in English or Hebrew ("Paris" / "פריז"): its GeoNames id and every
    name it goes by. The one in ``country`` when given, else the largest of that name; None if unknown."""
    city = _city(name, country, min_population)
    return (city[0], city[3]) if city else None


def city_zone(name: str, country: str) -> str | None:
    """Time zone of the city abroad called ``name`` (English or Hebrew) in ``country``; None if unknown."""
    city = _city(name, country, 0)
    return city[4] if city and city[1] == country else None
