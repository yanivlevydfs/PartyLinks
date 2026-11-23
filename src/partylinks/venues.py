"""Venue names: fill in the missing ones and merge spellings of the same place.

Every site (and every promoter) writes venues differently - "Gagarin Club TLV",
"מועדון גגרין" - and GO-OUT, ZYGO and Selector often give only a street address.
No alias lists: names are tied together by *where* they are.

* Names whose events sit at the same spot (within ``RADIUS_M``) or at the same
  street + number, or spelled alike but for case and punctuation, in one city, are one venue, shown under its most common real name (a name
  that is just an address, "שדרות רוטשילד 24", only wins when there is nothing else). A hall ("צוותא 2") is its venue.
* Links never chain from one place to another: a name found at several places ties none of them together,
  a name ties by street only at its own house number, and no venue grows past ``2 * max_spread``.
  A spot or street + number shared by several unrelated names (a building of venues: Ibn Gabirol 30 holds
  Tzavta, Gray, Bar Giyora and BABU; a site's default point) merges only the spellings of one name.
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
import statistics
from collections import Counter
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from partylinks.config import get_settings
from partylinks.db import EventRow, bulk_update_by_id
from partylinks.geo import RELATED_LETTERS, venue_key
from partylinks.parsing import slugify
from partylinks.places import PlaceIndex, all_cities

log = logging.getLogger(__name__)

# Geocoders put one place on (almost) one point; neighbouring clubs are ~50 m apart.
RADIUS_M = 25.0
# This many unrelated venues at one street + number: a building of venues (Ibn Gabirol 30: Tzavta, Gray,
# Bar Giyora, BABU) or a site's default address, where only the spellings of one name are one venue.
CROWD = 3
# A hall named after its venue: "צוותא 2", "תיאטרון הקאמרי - אולם 1", "בר גיורא, אבן גבירול 30".
_HALL_NUMBER = re.compile(r"^(?P<venue>.+?)\s+(?:אולם\s+|hall\s+)?\d$", re.IGNORECASE)
_AFTER_NAME = re.compile(r"\s+[-–—|]\s+|\s*,\s*")
_HALL_PART = re.compile(r"\d|אולם|סטודיו|\b(?:hall|studio|stage|room)\b", re.IGNORECASE)
_HALL_WORD = re.compile(r"(?:אולם|hall)\b", re.IGNORECASE)  # "אולם רקאנטי": a hall of whatever venue it is in
_VOWEL_LETTERS = re.compile(r"[וי]")  # written or left out: "תיאטרון" / "תאטרון"
_ARTICLE = re.compile(r"(?<!\S)ה(?=\S\S)")  # "אולם המופעים" / "אולם מופעים"
_LEADING_PAREN = re.compile(r"^\s*\([^)]*\)\s*")  # OGO: "(Nagler House) Ben-Zvi Yitzchak St 14, Haifa"
_HEBREW = re.compile(r"[א-ת]")
_LATIN = re.compile(r"[a-z]", re.IGNORECASE)
# Words a name adds around the venue in either script ("Gray Club", "מועדון הבארבי"); not "bar", it is in names.
_FILLER = re.compile(r"\b(?:club|lounge|tlv|the|tel[\s\-]*aviv(?:[\s\-]*yafo)?|מועדון|תל[\s\-]*אביב(?:[\s\-]*יפו)?)\b")
# Latin spellings of one sound ("č" is a hard "ch"; a plain "c" is s before e/i/y, else k).
_LATIN_SOUNDS = (("sch", "s"), ("ch", "č"), ("sh", "s"), ("th", "t"), ("ph", "p"), ("kh", "k"), ("tz", "s"), ("ts", "s"),
                 ("ck", "k"), ("qu", "k"), ("x", "ks"))  # fmt: skip
# Each letter as the consonant it sounds; vowels (and the Hebrew letters that write them) drop.
_SOUNDS = str.maketrans({
    **dict.fromkeys("aeiouyאויע", ""), "c": "k", "q": "k", "w": "b", "v": "b", "f": "p", "z": "s", "č": "c",
    "ב": "b", "ג": "g", "ד": "d", "ה": "h", "ז": "s", "ח": "h", "ט": "t", "כ": "k", "ך": "k", "ל": "l", "מ": "m",
    "ם": "m", "נ": "n", "ן": "n", "ס": "s", "פ": "p", "ף": "p", "צ": "s", "ץ": "s", "ק": "k", "ר": "r", "ש": "s",
    "ת": "t",
})  # fmt: skip
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
    """"דרך קיבוץ גלויות 52, ..." and "קיבוץ גלויות 52" -> same key; OGO's "(Tzavta Theatre) Shlomo Ibn Gabirol St 30" is
    the street's, not one per venue."""
    match = _STREET_IN_TEXT.search(_LEADING_PAREN.sub("", address or ""))
    if not match:
        return None
    street = slugify(_STREET_WORDS.sub(" ", match["street"]))
    return f"{street}-{match['number']}" if street else None


def street_name(address: str | None) -> str | None:
    """First address segment when it is exactly "street number" - usable as a venue name."""
    first = (address or "").split(",")[0].strip()
    return first if _STREET_NUMBER.match(first) else None


def _place(key: str) -> str:
    """A street key's last street word and number: "שלמה-אבן-גבירול-30" and "אבן-גבירול-30" are one address."""
    return "-".join(key.split("-")[-2:])


def _pin(r: VenueInput) -> tuple[float, float] | None:
    return None if r.lat is None or r.lng is None else (round(r.lat, 4), round(r.lng, 4))


def _place_only(name: str) -> bool:
    """A name that is an address ("שדרות רוטשילד 24", "בן אביגדור 19, תל אביב-יפו", "שדרות יצחק רגר, רובע החדשנות"):
    the place, no venue of its own. "Hangar 11" and "קאמרי 1, שדרות שאול המלך 19" are venues."""
    first, *rest = (part.strip() for part in name.split(","))
    if rest and _STREET_WORDS.match(first):
        return True
    return bool(_STREET_NUMBER.match(first)) and bool(_STREET_WORDS.search(first) or rest and not re.search(r"\d", "".join(rest)))


def _best(votes: Counter[str]) -> str | None:
    """Most common name, preferring real names over ones that are just an address."""
    real = Counter({n: c for n, c in votes.items() if not (_place_only(n) or _STREET_NUMBER.match(n))})
    return (real or votes).most_common(1)[0][0] if votes else None


def _key(name: str, town: str) -> str:
    """The venue key without the town's name ("תמוז באר שבע" -> "תמוז")."""
    key = venue_key(name) or slugify(name)
    bare = key.replace(town, "") if town else key
    return bare if len(bare) >= 3 else key


def _skeleton(name: str) -> str:
    """Consonants as they sound, Hebrew and Latin alike: "Stand up Factory" and "סטנדאפ פקטורי" -> "stndppktr"."""
    text = _FILLER.sub(" ", name.lower())
    text = re.sub(r"ה(?=\W|$)", "", re.sub(r"וו", "ב", re.sub(r"[צץ]['׳]", "č", re.sub(r"[גז]['׳]", "j", text))))
    for latin, sound in _LATIN_SOUNDS:
        text = text.replace(latin, sound)
    text = re.sub(r"c(?=[eiy])", "s", text).translate(_SOUNDS)
    return re.sub(r"[^a-z]", "", text)


def _alike(a: str, b: str) -> bool:
    """One key inside the other ("צוותא" / "צוותא1"), or RELATED_LETTERS letters in a row in common."""
    if min(len(a), len(b)) < 3:
        return a == b
    return a in b or b in a or any(a[i : i + RELATED_LETTERS] in b for i in range(len(a) - RELATED_LETTERS + 1))


def _sound_alike(a: str, b: str) -> bool:
    """Skeletons of one name in two scripts: the same, one inside the other, or five sounds in a row in common."""
    short, long = sorted((a, b), key=len)
    if len(short) < 3:
        return False
    return short == long or len(short) >= 4 and short in long or any(short[i : i + 5] in long for i in range(len(short) - 4))


def _letters(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.lower())


def _halls_of(name: str, town: str) -> list[str]:
    """Keys of the venue a hall may be named after: "צוותא 2", "תיאטרון הקאמרי תל אביב - החדש 1", "בר גיורא, אבן
    גבירול 30", "אולם 1- המשכן לאמנויות הבמה" -> the venue. A part with no number or hall word ("מרכז קהילתי -
    בבלי") is another place; a trailing town ("צוותא 2, תל אביב") is no part."""
    parts = _AFTER_NAME.split(name)
    while len(parts) > 1 and (not venue_key(parts[-1]) or bool(town) and _letters(parts[-1]).startswith(town)):
        parts.pop()
    name = " - ".join(parts)
    found = []
    if len(parts) > 1 and _HALL_PART.search(" ".join(parts[1:])):
        found.append(parts[0])
    if numbered := _HALL_NUMBER.match(name):
        found.append(numbered["venue"])
    if (hall := _HALL_WORD.match(name)) and (venue := re.sub(r"^[\s\d\-–—,]+", "", name[hall.end() :])):
        found.append(venue)
    return [key for venue in found if len(key := _key(venue, town)) >= 3]


def infer_venues(rows: list[VenueInput], max_spread: float = 0.001, crowd: int = CROWD) -> dict[str, str | None]:
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
    count = Counter(node[r.id] for r in named)
    town = {n: n[0].replace("-", "") for n in count}
    keys = {n: _key(n[1], town[n]) for n in count}
    plain = {n: _VOWEL_LETTERS.sub("", _key(_ARTICLE.sub("", n[1]), town[n])) for n in count}
    sounds = {n: _skeleton(n[1]) for n in count}
    scripts = {n: (bool(_HEBREW.search(n[1])), bool(_LATIN.search(n[1]))) for n in count}
    venues = {n for n in count if not _place_only(n[1])}
    halls = {n for n in venues if _HALL_WORD.match(n[1])}  # "אולם רקאנטי": its own venue only where it is alone
    # A name found all over town is no one place (a box office, "HaTarbut", a form's "הוספת ערך משלך ידנית"):
    # it keeps its name but ties nothing together, or it would chain every hall it sold for into one venue.
    pins: dict[Node, list[tuple[float, float]]] = {}
    for r in named:
        if (pin := _pin(r)) and located(r):
            pins.setdefault(node[r.id], []).append(pin)
    # Its spot is where most of its events are (within ``max_spread`` of their middle); a stray pin (one Cameri
    # show placed 1.6 km south) is elsewhere, and with no such majority the name is in no one place.
    spot: dict[Node, tuple[float, float]] = {}
    scattered: set[Node] = set()
    for n, at in pins.items():
        mid = (statistics.median(a[0] for a in at), statistics.median(a[1] for a in at))
        if sum(abs(a[0] - mid[0]) <= max_spread and abs(a[1] - mid[1]) <= max_spread for a in at) * 2 > len(at):
            spot[n] = mid
        else:
            scattered.add(n)

    def at_spot(r: VenueInput) -> bool:
        mid = spot.get(node[r.id])
        return mid is not None and abs(r.lat - mid[0]) <= max_spread and abs(r.lng - mid[1]) <= max_spread  # type: ignore[operator]
    # By street a name ties only at its own house number, the one most of its addresses give: its other
    # addresses ("המסגר 9" on a Cameri show) are elsewhere, and with no such number it is in no one place.
    numbers: dict[Node, Counter[str]] = {}
    for r in named:
        if key := street_key(r.address):
            numbers.setdefault(node[r.id], Counter())[key.rsplit("-", 1)[1]] += 1
    home: dict[Node, str] = {}
    for n, votes in numbers.items():
        number, votes_for = votes.most_common(1)[0]
        if votes_for * 2 > votes.total():
            home[n] = number
        else:
            scattered.add(n)
    # Each venue once per spot, bucketed by grid cell: neighbours are looked up, not scattered.
    grid: dict[tuple[int, int], dict[tuple[Node, tuple[float, float]], None]] = {}
    for r in named:
        if located(r) and at_spot(r):
            point = (r.lat, r.lng)
            grid.setdefault(_cell(point), {})[(node[r.id], point)] = None  # type: ignore[index]

    def near(point: tuple[float, float], cities: tuple[str, ...]) -> list[Node]:
        return list(dict.fromkeys(
            other
            for cell in _neighbours(_cell(point))
            for other, at in grid.get(cell, {})
            if other[0] in cities and _distance_m(point, at) <= RADIUS_M
        ))  # fmt: skip

    # Union-find over names in a city: same spot or same street + number -> same venue. Never across
    # cities: a producer's name ("הקאמרי") on its touring shows is not one venue country-wide.
    parent: dict[Node, Node] = {n: n for n in count}

    def find(n: Node) -> Node:
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    def union(a: Node, b: Node) -> None:
        parent[find(a)] = find(b)

    def spell_alike(a: Node, b: Node) -> bool:
        """Two spellings of one name: alike letters, or (in two scripts) alike sounds."""
        if _alike(keys[a], keys[b]) or _alike(plain[a], plain[b]):
            return True
        return scripts[a] != scripts[b] and _sound_alike(sounds[a], sounds[b])

    seen: dict[tuple[Node, ...], list[list[Node]]] = {}

    def venues_at(names: list[Node]) -> list[list[Node]]:
        """The venues among the names at one place, most common first, each with the names that spell it.
        A name that spells two of them (Tzavta's "צוותא BABU בר") is neither's, and a hall that spells none is
        a venue only where no other venue is."""
        ordered = tuple(sorted((n for n in names if n in venues), key=lambda n: (n in halls, -count[n], n[1])))
        if (found := seen.get(ordered)) is not None:
            return found
        found = []
        for n in ordered:
            spells = [group for group in found if any(spell_alike(m, n) for m in group)]
            if len(spells) == 1:
                spells[0].append(n)
            elif not spells and (n not in halls or all(group[0] in halls for group in found)):
                found.append([n])
        seen[ordered] = found
        return found

    by_slug: dict[tuple[str, str], Node] = {}
    by_street: dict[tuple[str, str], dict[Node, None]] = {}
    for r in named:
        # Spellings that differ only in case and punctuation ("בית העם - רחובות" / "בית העם רחובות.").
        if slug := slugify(r.source_name):
            union(node[r.id], by_slug.setdefault((node[r.id][0], slug), node[r.id]))
        key = street_key(r.address)
        if key and node[r.id] not in scattered and key.rsplit("-", 1)[1] == home.get(node[r.id]):
            by_street.setdefault((node[r.id][0], _place(key)), {})[node[r.id]] = None
    # A hall is its venue ("צוותא 2" -> "צוותא") when that venue is one place in the city.
    by_key: dict[tuple[str, str], Node] = {}
    for n in sorted(venues, key=lambda n: -count[n]):
        by_key.setdefault((n[0], keys[n]), n)
    for n in count:
        house = next((h for key in _halls_of(n[1], town[n]) if (h := by_key.get((n[0], key)))), None)
        if house and house != n and house not in scattered and n not in scattered:
            union(n, house)
    # An address is one place: the names there are one venue, unless ``crowd`` venues share it (a building of
    # venues, a site's default address) - then each venue takes only the names that spell it.
    for names in map(list, by_street.values()):
        if len(groups := venues_at(names)) < crowd:
            for other in names[1:]:
                union(names[0], other)
        else:
            for group in groups:
                for other in group[1:]:
                    union(group[0], other)
    # A spot is looser (neighbours are ~50 m apart, geocoders miss by tens): only spellings meet there, and an
    # address or a hall joins the venue of its spots only when they all have that one venue.
    lone: dict[Node, set[Node]] = {}
    torn: set[Node] = set()
    for cell in grid.values():
        for here, point in cell:
            names = [here, *(other for other in near(point, (here[0],)) if other != here)]
            groups = venues_at(names)
            for group in groups:
                for other in group[1:]:
                    union(group[0], other)
            grouped = {m for group in groups for m in group}
            for n in names:
                if n in grouped or not groups:
                    continue
                if len(groups) == 1:
                    lone.setdefault(n, set()).add(groups[0][0])
                else:
                    torn.add(n)  # several venues here: no telling which one it is
    for n, houses in lone.items():
        if n not in torn and len({find(house) for house in houses}) == 1:
            union(n, next(iter(houses)))

    clusters: dict[Node, Counter[str]] = {}
    for r in named:
        clusters.setdefault(find(node[r.id]), Counter())[r.source_name] += 1  # type: ignore[index]
    canonical = {n: _best(clusters[find(n)]) for n in parent}

    def one_venue(names: list[Node], shared_by: int) -> Node | None:
        """The venue at a place of these names, if it holds fewer than ``shared_by`` venues."""
        groups = venues_at(names)
        return (groups[0][0] if groups else names[0]) if names and len(groups) < shared_by else None

    result: dict[str, str | None] = {}
    for row in rows:
        here = node[row.id]
        if row.source_name:
            name = canonical[here]
        elif key := street_key(row.address):
            # The venue at the row's spot or street, in its city or one no source gave; none where venues share it.
            close = near((row.lat, row.lng), (here[0], "")) if located(row) else []  # type: ignore[arg-type]
            match = one_venue(close, 2) or next(
                (v for city in (here[0], "") if (v := one_venue(list(by_street.get((city, _place(key)), {})), crowd))), None
            )
            name = canonical[match] if match else street_name(row.address)
        else:
            name = None
        if name != row.venue_name:
            result[row.id] = name
    return result


def resolve_venues(session: Session) -> int:
    """Apply :func:`infer_venues` to the database; returns the number of events changed."""
    updates = venue_updates(session)
    bulk_update_by_id(session, EventRow, updates)
    log.debug("venues: renamed %d events", len(updates))
    return len(updates)


def venue_updates(session: Session) -> list[dict[str, Any]]:
    """The rows :func:`infer_venues` changes (read only: the caller writes them)."""
    db_rows = session.execute(
        select(
            EventRow.id, EventRow.venue_name, EventRow.address, EventRow.latitude, EventRow.longitude, EventRow.extra,
            EventRow.city, EventRow.venue_slug,
        )
    ).all()
    extras: dict[str, dict[str, Any]] = {r.id: dict(r.extra or {}) for r in db_rows}
    enrichment = get_settings().enrichment
    places = PlaceIndex(all_cities(enrichment.cities, enrichment.places_min_population))
    sources = {
        r.id: None if extras[r.id].get("venue_inferred") else extras[r.id].get(SOURCE_NAME, r.venue_name)
        for r in db_rows
    }

    def venue_said(name: str | None, city: str | None) -> str | None:
        """What the site said, unless it only named the town ("תל אביב"): that is no venue."""
        return None if name and (places.is_place(name) or slugify(name) == slugify(city)) else name

    inputs = [
        VenueInput(r.id, r.venue_name, venue_said(sources[r.id], r.city), r.address, r.latitude, r.longitude, r.city)
        for r in db_rows
        # A bar's own happy hour names its bar exactly: bars 20 m apart on Dizengoff stay apart, and
        # the bar's name never spreads to a club next door.
        if not extras[r.id].get(PLACE_LISTING)
    ]
    current = {r.id: (r.venue_name, r.venue_slug, r.extra or {}) for r in db_rows}
    updates = []
    for event_id, name in infer_venues(inputs, enrichment.venue_max_spread_degrees).items():
        extra = dict(extras[event_id])
        extra.pop("venue_inferred", None)  # marker of the previous version
        if name == sources[event_id]:
            extra.pop(SOURCE_NAME, None)
        else:
            extra[SOURCE_NAME] = sources[event_id]
        row = {"id": event_id, "venue_name": name, "venue_slug": slugify(name) or None, "extra": extra}
        if (name, row["venue_slug"], extra) != current[event_id]:  # unchanged rows are not rewritten
            updates.append(row)
    return updates
