"""Every Israeli locality from GeoNames (``partylinks.geonames``: geonamescache, read once, cached small).

``enrichment.cities`` in the config names the cities that get their own page and spelling; this
fills in the rest (Yehud, Shoham, Rosh Pina, ...) with a canonical Hebrew name, a centre and a
radius, so an address in any town gets its city and a map point outside that town is rejected.

Names are found by word lookups in a dict, not one regex per town: an address costs a few dict
probes whatever the number of towns (28k events x ~200 towns of regexes took seconds).
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from difflib import get_close_matches
from functools import lru_cache
from pathlib import Path

from partylinks import geonames
from partylinks.config import CitySettings
from partylinks.parsing import slugify

_HEBREW = re.compile(r"[֐-׿]")
_NIQQUD = re.compile(r"[֑-ׇ]")  # vowel and cantillation marks: "סַבְיוֹן" -> "סביון"
_WORD = re.compile(r"[\w'׳\"״]+")
_PREFIXES = "ובהלמשכ"
MAX_WORDS = 4  # "קריית שמונה", "מודיעין מכבים רעות"


def _norm(text: str) -> str:
    text = text.lower().replace("־", " ").replace("-", " ").replace("–", " ").replace("״", '"').replace("׳", "'")
    return " ".join(_WORD.findall(text))


def _radius_m(population: int) -> float:
    """~9 km for Tel Aviv, ~3 km for Yehud, ~1.5 km for a small town."""
    return 1000 + 12 * math.sqrt(population)


@lru_cache(maxsize=1)
def _official() -> tuple[dict[str, str], dict[str, str]]:
    """The State's list of localities (src/partylinks/localities.json, scripts/fetch_localities.py): each part of
    an official name ("פקיעין (בוקייעה)": the whole, "פקיעין", "בוקייעה") -> the official name, and its English
    name likewise."""
    rows = json.loads((Path(__file__).with_name("localities.json")).read_text(encoding="utf-8"))["localities"]
    he: dict[str, str] = {}
    en: dict[str, str] = {}
    for row in rows:
        for part in _name_parts(row["he"]):
            he.setdefault(_he_key(part), row["he"])
        for part in _name_parts(row["en"]):
            if part:
                en.setdefault(_en_key(part), row["he"])
    return he, en


def _name_parts(name: str) -> list[str]:
    """A name and, when it has one, its bracketed other name: "ג'ש (גוש חלב)" -> itself, "ג'ש", "גוש חלב"."""
    m = re.match(r"(.*?)\s*\((.*)\)\s*$", name)
    return [name, *(p.strip() for p in m.groups())] if m else [name]


def _he_key(name: str) -> str:  # the geresh stays: ג'ת (Jatt) is not גת
    name = re.sub(r"[׳’‘`]", "'", name).replace("״", '"')
    return re.sub(r"[\s\-־–]+", " ", name).strip()


def _en_key(name: str) -> str:
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower())


def official_name(hebrew: list[str], english: str) -> str | None:
    """The official Hebrew name of a GeoNames town: by one of its Hebrew names, else by its English name (the
    same, or nearly: "Umm el Faḥm" / "UMM AL-FAHM"). None: a place the State's list does not have (Jaffa)."""
    he, en = _official()
    if found := next((he[_he_key(h)] for h in hebrew if _he_key(h) in he), None):
        return found
    key = _en_key(english)
    if key in en:
        return en[key]
    close = get_close_matches(key, list(en), n=2, cutoff=0.85) if key else []
    return en[close[0]] if len(close) == 1 else None


@lru_cache(maxsize=4)
def israeli_places(min_population: int) -> tuple[CitySettings, ...]:
    """Localities of at least ``min_population`` (smaller ones are often words: "מגדל", "כנרת")."""
    places = []
    for c in geonames.israel():  # loaded once per process, from a small cache file (partylinks.geonames)
        if c["population"] < min_population:
            continue
        hebrew = list(dict.fromkeys(_NIQQUD.sub("", a) for a in c["alternatenames"] if _HEBREW.search(a)))
        if not hebrew:
            continue
        # Its name in the State's list (פקיעין (בוקייעה), not GeoNames' first: "אל-בוקיעה", "מפשטה" for Fassuta);
        # every other name, and each part of the official one, stays a way to find it.
        if official := official_name(hebrew, c["name"]):
            hebrew = [official, *_name_parts(official)[1:], *(h for h in hebrew if h != official)]
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

    def is_place(self, text: str | None) -> bool:
        """``text`` is a town's name and nothing else."""
        return _norm(text or "") in self._names

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


@lru_cache(maxsize=1)
def _latin_towns() -> dict[str, tuple[str, str]]:
    """Latin name -> (GeoNames name, Hebrew name) of the biggest Israeli locality (500+) called that.
    The Hebrew name is the first of GeoNames' Hebrew ones with the most words ("עצמון שגב", not "עצמון")."""
    out: dict[str, tuple[str, str]] = {}
    for c in sorted(geonames.israel(), key=lambda c: -c["population"]):
        hebrew = [_NIQQUD.sub("", a).strip("\u200e\u200f ") for a in c["alternatenames"] if _HEBREW.search(a)]
        if not hebrew:
            continue
        he = max(hebrew, key=lambda h: len(h.split()))
        for name in (c["name"], *c["alternatenames"]):
            if not _HEBREW.search(name) and (key := _latin_key(name)):
                out.setdefault(key, (c["name"], he))
    return out


def _latin_key(name: str) -> str:
    """Latin spelling without accents and apostrophes: "Yif‘at", "Yif'at", "yifat" are one key."""
    return _norm(unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().replace("'", "").replace("`", ""))


def latin_town(name: str) -> tuple[str, str] | None:
    """The Israeli locality a whole Latin city field names ("kfar blum", "Meishar"), any size: its
    GeoNames name and its Hebrew name. Whole field only: in free text a small town's name is often a word."""
    return _latin_towns().get(_latin_key(name)) if name.isascii() else None


def all_cities(configured: list[CitySettings], min_population: int) -> list[CitySettings]:
    """The configured cities, then every GeoNames town none of them already names."""
    known = PlaceIndex(configured)
    extra = [p for p in israeli_places(min_population) if not any(known.find(n) for n in (p.he, p.en))]
    return [*configured, *extra]


# (GeoNames id, country, population, its names, its time zone, its centre)
_World = tuple[int, str, int, tuple[str, ...], str, tuple[float, float]]


@lru_cache(maxsize=1)
def _world_cities() -> dict[str, list[_World]]:
    """Every city abroad of 15,000+ people by each of its names (English, Hebrew), normalised:
    [(GeoNames id, country, population, its names, its time zone, its centre)]. Loaded once per process."""
    index: dict[str, list[_World]] = {}
    for c in geonames.world():  # loaded once per process, from a small cache file (partylinks.geonames)
        hebrew = [_NIQQUD.sub("", a) for a in c["alternatenames"] if _HEBREW.search(a)]
        names = tuple(dict.fromkeys([c["name"], *hebrew]))
        for name in names:
            index.setdefault(_norm(name), []).append(
                (int(c["geonameid"]), c["countrycode"], c["population"], names, c["timezone"], (c["latitude"], c["longitude"]))
            )
    return index


_PARTS = re.compile(r"[,()/|]")


def _city(name: str, country: str | None, min_population: int) -> _World | None:
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


def city_name(name: str, country: str | None) -> str:
    """``name`` without the extras sellers add ("ברלין, גרמניה" -> "ברלין", "BERLIN, GERNANY" -> "Berlin"):
    the part that names a known city, in its own script (a Latin one in GeoNames' spelling). A known
    name or an unknown place is kept as given."""
    cities = _world_cities()
    if cities.get(_norm(name)):
        return name
    city = _city(name, country, 0)
    if city is None:
        return name
    part = next((p.strip() for p in _PARTS.split(name) if any(c[0] == city[0] for c in cities.get(_norm(p), ()))), name)
    return part if _HEBREW.search(part) else city[3][0]


def city_zone(name: str, country: str) -> str | None:
    """Time zone of the city abroad called ``name`` (English or Hebrew) in ``country``; None if unknown."""
    city = _city(name, country, 0)
    return city[4] if city and city[1] == country else None


def city_point(name: str, country: str | None, min_population: int = 0) -> tuple[float, float] | None:
    """Centre of the city abroad called ``name`` (English or Hebrew) in ``country``: the largest of that
    name there ("Freiburg", DE: Freiburg im Breisgau, not the small Freiburg on the Elbe); None if unknown
    or under ``min_population``. No ``country``: the largest city of that name anywhere."""
    city = _city(name, country, min_population)
    return city[5] if city and (country is None or city[1] == country) else None
