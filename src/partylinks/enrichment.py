"""Post-scrape normalization and categorization.

Adds canonical genres, the DJ/performer lineup, local date / weekday / time slot,
free-vs-paid entry, target-city matching and the area of the city. It runs on finished
``ScrapedEvent`` objects only, so it never affects how any source is fetched.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable, Iterable
from datetime import datetime, time
from decimal import Decimal
from difflib import SequenceMatcher
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from partylinks.config import AreaSettings, CitySettings, EnrichmentSettings, RegionSettings
from partylinks.models import EntryType, ScrapedEvent, TicketKind
from partylinks.parsing import slugify
from partylinks.places import PlaceIndex, all_cities, city_name, city_point, israeli_places, latin_town

_EDGE_DECOR = re.compile(r"^[^\w'\"(]+|[^\w'\")]+$")
_LEADING_WITH = re.compile(r"^(?:w/|with|feat\.?|ft\.?)\s+", re.IGNORECASE)
_DATE_OR_TIME = re.compile(r"\d{1,2}\s*[./:]\s*\d{1,2}")
_LIST_SPLIT = re.compile(r"\s*(?:,|\||•|/|·)\s*")
# Names on one lineup line ("Remat Siaq · Tina · Orabi"); not "/", which joins a name and its handle.
_LINE_SPLIT = re.compile(r"\s*(?:,|\||•|·)\s*")
# "DJ NAME" written inline in running text: upper-case words on the same line ("DJ Mag" / "DJ Night" are not names).
_DJ_WORD = r"[A-ZÀ-Þ0-9](?:[\w&'’-]|\.(?=\w))*"  # "MR.BLACK" keeps its dot; "Mag." ends the name
_INLINE_DJ = re.compile(rf"\bDJ[^\S\n]+({_DJ_WORD}(?:[^\S\n]+{_DJ_WORD}){{0,2}})")
# Back-to-back sets are separate artists: "LANDAU B2B ANSTY", "NYx × Ansty".
_COLLAB = re.compile(r"\s+(?:b2b|f2f|vs\.?|x|×)\s+|\s+b2b(?=[A-Z])", re.IGNORECASE)
# Notes after a name: "rotemx {Q}", "Ravid Polianski (Birthday)", "distørpya {GERMANY" (closer already stripped).
_ANNOTATION = re.compile(r"\s*[(\[{].*$")
_TBA = re.compile(r"(?:\s+and)?\s*more\s+tba\b.*$|\s*\b(?:tba|t\.b\.a\.?)(?:\s.*)?$", re.IGNORECASE)
# A timetable slot before a name: "23:00 - 00:00 VICTORIA", "20:00 - זיו יחזקאל", "17:30-18:30SEAN KEE".
# A clock in any of the sellers' ways: "23:00", "21h45", "21H00", "12AM", "10PM", "1:30am".
# A bare hour ("2AM", "21h") is a clock only in a range ("1AM–2AM"): "2AM CLUB" is a band.
_CLOCK_FULL = r"\d{1,2}[:.h]\d{2}\s*(?:[ap]\.?m\.?)?"
_CLOCK_SHORT = r"\d{1,2}(?:h|\s*[ap]\.?m\.?)"
_CLOCK_ANY = rf"(?:{_CLOCK_FULL}|{_CLOCK_SHORT})"
_SLOT = re.compile(
    rf"^\W*(?:{_CLOCK_FULL}(?:\s*[-–—~]\s*{_CLOCK_ANY})?|{_CLOCK_SHORT}\s*[-–—~]\s*{_CLOCK_ANY})(?!\d)\s*[-–—:|]?\s*",
    re.IGNORECASE,
)
# List numbering before a name: "01] DJ IAGO", "3) NOA".
_NUMBERING = re.compile(r"^\s*\d{1,2}\s*[\])]\s*")
# Not a name at all: underscores ("______ BLACK", "_music"), "???", a postal code or a street ("16 Rue Saglio
# 67100 Strasbourg", "6 Impasse Philippe Lebon").
_NOT_A_NAME = re.compile(
    r"__|^_|\?\?|\b\d{5}\b|\b\d{1,2}h\b|\b\d{1,2}[h:]\d{2}\b|\b(?:rue|impasse|avenue|boulevard|chemin|allée|straße|strasse|calle|via)\b", re.IGNORECASE
)
_DJ_DASH = re.compile(r"^dj\s*[-–—:]\s*", re.IGNORECASE)
_HEBREW = re.compile(r"[֐-׿].*$")
_SENTENCE_END = (".", "!", "?", "…")


def _words_pattern(words: list[str]) -> str:
    """Regex alternation of whole words (matches nothing for an empty list)."""
    return rf"\b(?:{'|'.join(re.escape(w) for w in words if w.strip()) or '(?!)'})\b"


def _keyword_pattern(keywords: list[str]) -> re.Pattern[str] | None:
    """ASCII keywords match on word boundaries; Hebrew ones as substrings
    (Hebrew attaches prefixes like ה/ב/ל directly to words)."""
    parts = [
        rf"(?<![a-z0-9]){re.escape(k.lower())}(?![a-z0-9])" if k.isascii() else re.escape(k.lower())
        for k in keywords
        if k.strip()
    ]
    return re.compile("|".join(parts)) if parts else None


# Addresses in a description: their words are not genres (an email at bg-house.org, a link to techno-tlv.com).
_LINKS = re.compile(r"\S+@\S+|https?://\S+|www\.\S+|\b[\w-]+(?:\.[\w-]+)*\.(?:com|org|net|co|io|il|info|me|app)\b\S*")

OUTSIDE_AREA = "outside"
# A city name with no country is taken for a city abroad only when that city is this big (no village guesses).
TOWN_ABROAD_MIN_POPULATION = 100_000
ABROAD_AREA = "abroad"
# ISO 3166 "unknown": an event abroad whose country the source does not give. Any country code
# keeps an event out of the Israeli feed and in the abroad section.
UNKNOWN_COUNTRY = "ZZ"
LOCAL_COUNTRIES = {"IL", "PS", UNKNOWN_COUNTRY}  # an event in one of these is placed by its town


def meters(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Equirectangular distance: plenty accurate inside one city."""
    lat = math.radians((a[0] + b[0]) / 2)
    dx = math.radians(b[1] - a[1]) * math.cos(lat)
    dy = math.radians(b[0] - a[0])
    return 6_371_000 * math.hypot(dx, dy)



def _same_act(a: str, b: str) -> bool:
    """One event's lineup naming one act twice in two spellings ("Yamaguchi" / "YAMAGUCCI"): long names,
    same start, one or two letters apart. Two different artists of one lineup are never this close."""
    a, b = a.casefold(), b.casefold()
    return a == b or (min(len(a), len(b)) >= 6 and a[:3] == b[:3] and SequenceMatcher(None, a, b).ratio() >= 0.88)

class AreaMatcher:
    """Area of the target city, or region of the country outside it, for an event
    (also used to backfill stored rows)."""

    def __init__(
        self,
        areas: list[AreaSettings],
        regions: list[RegionSettings] | None = None,
        town_point: Callable[[str], tuple[float, float] | None] | None = None,
    ) -> None:
        # The centre of a town by name, for an event outside the target city that has no coordinates.
        self._town_point = town_point
        self._areas = [(area, _keyword_pattern(area.keywords)) for area in areas]
        self._regions = [(r, _place_pattern(r.places)) for r in regions or []]
        # In a venue name or a street address these are rarely the place: "תיאטרון הצפון" (Haifa),
        # "אולם גולן", "אילת 11 תל אביב", "שדרות ויצמן", "אשכול פיס בת ים" - correct points were
        # cleared as contradicting their region every pass (Oct 2026). The city field still counts.
        self._regions_in_names = [
            (r, _place_pattern([p for p in r.places if p not in NOT_A_PLACE_IN_NAMES])) for r in regions or []
        ]
        self.region_keys = {r.key for r in regions or []} | {ABROAD_AREA, OUTSIDE_AREA}

    def match(
        self,
        venue_name: str | None,
        address: str | None,
        latitude: float | None,
        longitude: float | None,
        in_target_city: bool | None,
        city: str | None = None,
        country: str | None = None,
    ) -> str | None:
        if not self._areas:
            return None
        if in_target_city is False or (in_target_city is None and country and country not in LOCAL_COUNTRIES):
            return self.region(city, venue_name, address, latitude, longitude, country)
        if key := self.keyword_area(venue_name, address):
            return key
        if latitude is None or longitude is None:
            return None
        here = (latitude, longitude)
        near = [(meters(here, a.center), a.key) for a, _ in self._areas if a.center]
        best = min(((d, key) for d, key in near if d <= self._radius(key)), default=None)
        return best[1] if best else None

    def region(
        self, city: str | None, venue_name: str | None, address: str | None,
        latitude: float | None, longitude: float | None, country: str | None = None,
    ) -> str:
        """Region of an event outside the target city: a named place (city first), a country abroad,
        then coordinates (the town's centre when the event has none)."""
        if named := self.named_region(city, None, None):
            return named.key
        if country and country not in LOCAL_COUNTRIES:
            return ABROAD_AREA
        # A known town without coordinates: its centre beats a street or venue word ("רחוב לוטם", Harish).
        town = self._town_point(city) if (latitude is None or longitude is None) and city and self._town_point else None
        if not town and (named := self.named_region(None, venue_name, address)):
            return named.key
        latitude, longitude = town or (latitude, longitude)
        if latitude is None or longitude is None or not self._regions:
            # Nothing else to go by: even "צפון" in "מחוז הצפון" is the place.
            last = next((r for text in (address, venue_name) for r, pattern in self._regions
                         if text and pattern and pattern.search(text.lower())), None)  # fmt: skip
            return last.key if last else OUTSIDE_AREA
        for region, _ in self._regions:
            if _in_boxes(region, latitude, longitude):
                return region.key
        return ABROAD_AREA

    def in_israel(self, latitude: float, longitude: float) -> bool:
        """Inside one of the regions' boxes (no boxes configured: anywhere counts)."""
        boxed = [r for r, _ in self._regions if r.boxes]
        return not boxed or any(_in_boxes(r, latitude, longitude) for r in boxed)

    def named_region(self, city: str | None, venue_name: str | None, address: str | None) -> RegionSettings | None:
        """Region of the first place named in the city, address or venue name (in that order); in the
        address and venue name not the words that are usually streets ("שדרות עמק איילון", Shoham)."""
        for text, regions in ((city, self._regions), (address, self._regions_in_names), (venue_name, self._regions_in_names)):
            for region, pattern in regions:
                if text and pattern and pattern.search(text.lower()):
                    return region
        return None

    def contradicts(
        self, city: str | None, venue_name: str | None, address: str | None, latitude: float, longitude: float
    ) -> bool:
        """True when the text names a region whose boxes miss the point ("קיבוץ הגושרים" placed in the Negev)."""
        named = self.named_region(city, None, None) or next(
            (r for text in (address, venue_name) for r, pattern in self._regions_in_names
             if text and pattern and pattern.search(text.lower())),
            None,
        )  # fmt: skip
        return named is not None and bool(named.boxes) and not _in_boxes(named, latitude, longitude)

    def keyword_area(self, venue_name: str | None, address: str | None) -> str | None:
        """Area named by a street / neighbourhood keyword in the text, ignoring coordinates."""
        text = " | ".join(filter(None, [venue_name, address])).lower()
        for area, pattern in self._areas:
            if text and pattern and pattern.search(text):
                return area.key
        return None

    def center(self, key: str) -> tuple[float, float] | None:
        return next((a.center for a, _ in self._areas if a.key == key), None)

    def _radius(self, key: str) -> float:
        return next(a.radius_m for a, _ in self._areas if a.key == key)


# An amount in a tier name ("מזומן בכניסה 180₪", "כרטיס מזומן 120"); never a time like 23:30.
_AMOUNT = re.compile(r"(?<![\d:./])(\d{2,4})(?![\d:./])")


def _amount(text: str) -> Decimal | None:
    match = _AMOUNT.search(re.sub(r"\([^)]*\)", " ", text))  # "(מוגבל 50)" is a quantity, not a price
    return Decimal(match.group(1)) if match else None


def _strip_decor(line: str) -> str:
    return _EDGE_DECOR.sub("", line.strip()).strip()


class Enricher:
    def __init__(self, settings: EnrichmentSettings, timezone: str) -> None:
        self.settings = settings
        self.tz = ZoneInfo(timezone)
        self._genres = {
            genre: pattern for genre, words in settings.genres.items() if (pattern := _genre_pattern(_unquoted(words)))
        }
        self._declared = {
            genre: pattern
            for genre, words in settings.declared_genres.items()
            if (pattern := _genre_pattern(_unquoted(words)))  # a declared "organic house" is no "organic" chill
        }
        self._talk = _place_pattern(_unquoted(settings.talk_keywords), prefixes=2)
        self._fallback = {
            genre: pattern
            for genre, words in settings.fallback_genres.items()
            if (pattern := _place_pattern(_unquoted(words), prefixes=2))
        }
        self._text_ignore = _unquoted([p.lower() for p in settings.text_ignore])
        self._free = _keyword_pattern(settings.free_entry_keywords)
        self._pay_at_door = _keyword_pattern(settings.pay_at_door_keywords)
        self._coupon = _keyword_pattern(settings.coupon_keywords)
        self._city = _place_pattern(settings.target_city.aliases)
        self._cities = [(c.he, p) for c in settings.cities if (p := _place_pattern([c.he, c.en, *c.aliases]))]
        extra = [CitySettings(key=slugify(he) or he, he=he, en=he, aliases=a) for he, a in settings.city_aliases.items()]
        self._places = PlaceIndex([*all_cities(settings.cities, settings.places_min_population), *extra])
        self._city_prefix = (
            re.compile(rf"^(?:{'|'.join(map(re.escape, settings.city_prefixes))})\s+", re.IGNORECASE)
            if settings.city_prefixes else None
        )  # fmt: skip
        self._not_cities = {c.casefold() for c in settings.not_cities}
        self._lineup_header = _keyword_pattern(settings.lineup.header_keywords)
        self._lineup_ignore = {w.casefold() for w in settings.lineup.ignore}
        self._genre_words = {w.casefold() for words in settings.genres.values() for w in words}
        lineup = settings.lineup
        self._header_filler = {w.casefold() for w in lineup.header_filler}
        past = _keyword_pattern(lineup.past_guest_words)  # matched on the text as written: IGNORECASE
        self._past_guests = re.compile(past.pattern, re.IGNORECASE) if past else None
        self._inline_dj_stop = {w.casefold() for w in lineup.inline_dj_stopwords}
        self._not_a_name = re.compile(_words_pattern(lineup.not_name_words), re.IGNORECASE)
        # "AZVELOS - Birthday Set", "KOSTA retro set", "NARDO WICK — MAIN EVENT", "EPIPHONY LIVE SHOW"
        qualifiers = "|".join(re.escape(w) for w in lineup.set_qualifiers) or "(?!)"
        self._set_suffix = re.compile(
            rf"\s*(?:[-–—]\s*)?(?:(?:{qualifiers})\s+)?{_words_pattern(lineup.set_words)}.*$", re.IGNORECASE
        )
        # Artist dictionary, loaded by the service from artists.py (empty when enriching standalone).
        self._known: set[str] = set()
        self._blocked: set[str] = set()
        self._featured: tuple[re.Pattern[str] | None, dict[str, str]] = (None, {})
        self._weekend = {d.lower() for d in settings.weekend_days}
        # Every GeoNames locality (500+ people) by its Hebrew names: the city field names a town outright,
        # so the small ones that are also words ("הזורע", "כפר שמריהו") are safe here.
        self._town_centers = {
            c.he: c.center for c in [*israeli_places(0), *all_cities(settings.cities, settings.places_min_population)]
            if c.center
        } | {a: c.center for c in israeli_places(0) for a in c.aliases if c.center}
        self.areas = AreaMatcher(settings.areas, settings.regions, self.town_point)

    def town_point(self, city: str) -> tuple[float, float] | None:
        """Centre of the town ``city`` names: an Israeli one (GeoNames), else a big city abroad ("Milan")."""
        return self._town_centers.get(city) or city_point(city, None, min_population=TOWN_ABROAD_MIN_POPULATION)

    def is_dropped(self, event: ScrapedEvent) -> bool:
        """True when enrich() would drop the event (another city while focusing on one)."""
        return self.settings.target_city.drop_other_cities and self.in_target_city(event.city, event.address, event.venue_name) is False

    def enrich(self, event: ScrapedEvent) -> ScrapedEvent | None:
        """Return the enriched event, or ``None`` if config says to drop it."""
        title = event.title.casefold()
        if any(skip.casefold() in title for skip in self.settings.skip_titles):
            return None
        self._slugs(event)
        self._drop_social_purchase(event)
        event.genres = self._detect_genres(event)
        event.lineup = self._lineup(event)
        self._mark_free_tiers(event)
        event.entry_type = self._entry_type(event)
        # Free entry = a free ticket you can still get; a sold-out event offers none.
        all_gone = event.is_sold_out or (bool(event.tickets) and not any(t.is_active for t in event.tickets))
        event.has_free_entry = not all_gone and event.entry_type in (EntryType.FREE, EntryType.FREE_AND_PAID)
        event.free_entry_until = _free_entry_until(event) if event.has_free_entry else None
        event.free_sold_out = not event.has_free_entry and any(
            t.kind is TicketKind.FREE and not t.is_active for t in event.tickets
        )
        event.has_discount = any(
            t.is_active and t.price is not None and t.original_price is not None and t.original_price > t.price
            for t in event.tickets
        ) or bool(self._coupon and self._coupon.search((event.description or "").lower()))
        self._categorize_time(event)
        if event.venue_name and len(event.venue_name) > self.settings.venue_name_max_chars:  # Makore's film synopsis
            event.venue_name = None
        event.city = self.canonical_city(event.city, event.address, event.venue_name)
        if event.city and event.country and event.country != UNKNOWN_COUNTRY:  # "ברלין, גרמניה" -> "ברלין"
            event.city = city_name(event.city, event.country)
        event.in_target_city = self.in_target_city(event.city, event.address, event.venue_name)
        event.area = self.areas.match(
            event.venue_name, event.address, event.latitude, event.longitude, event.in_target_city, event.city,
            event.country,
        )
        if event.area == ABROAD_AREA and not event.country:  # coordinates outside Israel (Go-Out abroad)
            event.country = UNKNOWN_COUNTRY
        if event.in_target_city is False and self.settings.target_city.drop_other_cities:
            return None
        return event

    # ------------------------------------------------------------------ slugs
    def _slugs(self, event: ScrapedEvent) -> None:
        """Stable, unique slug: <title>-<local date>-<source>-<short id>."""
        day = event.start_at.astimezone(self.tz).date().isoformat() if event.start_at else None
        digest = hashlib.sha1(event.event_id.encode()).hexdigest()[:8]
        parts = [slugify(event.title, 60), day, event.source, digest]
        event.slug = "-".join(p for p in parts if p)
        event.venue_slug = slugify(event.venue_name) or None

    # ------------------------------------------------------------------ genres
    def _detect_genres(self, event: ScrapedEvent) -> list[str]:
        ignore = tuple(p.lower() for p in self.settings.declared_ignore)
        declared = " | ".join(d for d in (x.lower() for x in [*event.source_genres, *event.event_types]) if not d.startswith(ignore))
        declared = declared.translate(_QUOTES)
        talk = self._talk is not None and self._talk.search(event.title.lower().translate(_QUOTES))
        read_text = self.settings.detect_genres_from_text and event.source not in self.settings.declared_genres_only
        # A talk's description names its subject, not its kind: only its title is read, for the talk genres.
        text = (event.title if talk else f"{event.title}\n{event.description or ''}").lower() if read_text else ""
        text = _LINKS.sub(" ", text).translate(_QUOTES)  # "info@bg-house.org" is not house music
        for phrase in self._text_ignore:
            text = text.replace(phrase, " ")
        found = {genre for genre, pattern in self._genres.items() if pattern.search(declared)}
        # "tech house" is music, not the declared category "tech".
        found |= {genre for genre, pattern in self._declared.items() if pattern.search(declared.replace("tech house", ""))}
        from_text = {genre for genre, pattern in self._genres.items() if pattern.search(text)}
        declared_only = set(self.settings.expand_genres(self.settings.declared_text_genres))
        if not found & declared_only:
            title = event.title.lower().translate(_QUOTES)
            for phrase in self._text_ignore:
                title = title.replace(phrase, " ")
            from_text -= {g for g in declared_only if g not in self._genres or not self._genres[g].search(title)}
        # A tech talk or conference ("Israeli startups", "the music industry's AI") has a subject, not a
        # music genre: its text is read only for the talk genres.
        talk_genres = set(self.settings.expand_genres(self.settings.talk_genres))
        title_only = set(self.settings.title_genres)
        title = event.title.lower().translate(_QUOTES)
        from_text -= {g for g in title_only if g in self._genres and not self._genres[g].search(title)}
        if talk:  # its title: the talk genres and the title-only ones ("סדנת בישול", "הרצאה על...")
            from_text &= talk_genres | title_only
        elif (found | from_text) & talk_genres:
            from_text &= talk_genres
        # "hard techno, neo-rave, hardcore" is hardstyle; "punk hardcore" is not.
        for genre, rule in self.settings.declared_genres_beside.items():
            near = found | from_text
            without = self.settings.expand_genres(rule.get("without", []))
            if (genre in self._declared and self._declared[genre].search(text)
                    and near & set(self.settings.expand_genres(rule.get("with", [])))
                    and not any(p.search(text) for g in without for p in (self._genres.get(g), self._declared.get(g)) if p)):
                from_text.add(genre)
        if found | from_text:
            return sorted(found | from_text)
        whole = _LINKS.sub(" ", f"{event.title}\n{event.description or ''}".lower()).translate(_QUOTES)
        for phrase in self._text_ignore:
            whole = whole.replace(phrase, " ")
        # The show's own words first ("תזמורת" at a culture hall is a concert), then the source's usual kind.
        if fallback := sorted(g for g, pattern in self._fallback.items() if pattern.search(whole)):
            return fallback
        return list(self.settings.source_default_genres.get(event.source, []))

    # ------------------------------------------------------------------ lineup
    def _lineup(self, event: ScrapedEvent) -> list[str]:
        names = [*event.artists, *self._lineup_from_text(event.description), *self._featured_in(event)]
        venue = (event.venue_name or "").casefold()
        unique: dict[str, str] = {}
        for raw in names:
            for part in (p for piece in _COLLAB.split(raw) for p in self._unglue(piece)):
                name = self._as_name(part)
                # "Haoman 17" is the venue, not a DJ; one act in two spellings is kept once.
                if name is not None and name.casefold() not in venue and not any(_same_act(name, k) for k in unique.values()):
                    unique[name.casefold()] = name
        return list(unique.values())[: self.settings.lineup.max_names]

    def learn_artists(self, known: Iterable[str], blocked: Iterable[str] = (), featured: Iterable[str] = ()) -> None:
        """Artist dictionary: single-word known names split glued ones; blocked names are never artists;
        featured (curated, e.g. Go-Out's artists) are also spotted anywhere in the title / description."""
        min_len = self.settings.lineup.min_known_length
        self._known = {k.casefold() for k in known if " " not in k.strip() and len(k.strip()) >= min_len}
        self._blocked = {b.casefold() for b in blocked}
        # A name that is also a genre word ("Melodic" in "melodic techno") or very short ("ko") is not spotted,
        # nor a one-word name with no Latin letter: Hebrew has no capitals to tell "חיים" / "כהן" from plain words.
        genre_parts = {part for words in self._genre_words for part in words.split()}
        spot = sorted(
            {n.strip() for n in featured
             if len(n.strip()) >= min_len and n.strip().casefold() not in genre_parts | self._blocked
             and (" " in n.strip() or re.search(r"[A-Za-z]", n))},
            key=len, reverse=True,
        )  # fmt: skip
        # Multi-word names match in any case; one-word names only as written or in capitals ("Dali" / "DALI").
        parts = [
            rf"(?i:{re.escape(n)})" if " " in n else rf"{re.escape(n)}|{re.escape(n.upper())}" for n in spot
        ]
        self._featured = (
            re.compile(rf"(?<![\w&]){'(?:' + '|'.join(parts) + ')'}(?![\w&])") if parts else None,
            {n.casefold(): n for n in spot},
        )

    def _featured_in(self, event: ScrapedEvent) -> list[str]:
        pattern, spelling = self._featured
        if pattern is None:
            return []
        text = f"{event.title}\n{event.description or ''}"
        if self._past_guests:
            text = self._drop_past_guests(text)
        # (?i) also equates i / ı / İ, whose casefolds differ ("YILDIRIM" for "Yıldırım"): keep it as written.
        return [spelling.get(m.group(0).casefold(), m.group(0)) for m in pattern.finditer(text)]

    def _drop_social_purchase(self, event: ScrapedEvent) -> None:
        """An Instagram post or a Facebook event is no place to buy: purchase links keep real sellers only."""
        link = event.extra.get("purchase_url")
        host = (urlsplit(link).hostname or "").lower() if isinstance(link, str) else ""
        if host and any(host == h or host.endswith("." + h) for h in self.settings.social_hosts):
            event.extra.pop("purchase_url")

    def _drop_past_guests(self, text: str) -> str:
        """Cut the sentences on the promoter's history ("לאורך השנים אירחו את Seth Troxler, Lee Burridge"),
        and the list after one that ends in ":" ("במהלך השנים אירחנו את:\\nMiss Monique, Korolova") up to the
        next blank line: those names are not tonight's."""
        assert self._past_guests is not None
        kept: list[str] = []
        in_list = False
        for line in text.split("\n"):
            if not line.strip():
                in_list = False
            elif in_list:
                continue
            elif hit := self._past_guests.search(line):
                start = max(line.rfind(c, 0, hit.start()) for c in ".!?…") + 1
                ends = [i for c in ".!?…" if (i := line.find(c, hit.end())) >= 0]
                in_list = not ends and line.rstrip().endswith(":")
                line = line[:start] + (line[min(ends) + 1 :] if ends else "")
            kept.append(line)
        return "\n".join(kept)

    def _unglue(self, name: str) -> list[str]:
        """"MOSKOMILLEROADARU" -> MOSKO, MILLERO, ADARU when MILLERO is a known artist.

        ponytail: substring match against known names, so a real name that contains a known one
        gets split; block the wrong pieces in the artists DB. Upgrade path: a per-artist allowlist.
        """
        words = name.split()
        for i, word in enumerate(words):
            pieces = self._split_word(word)
            if len(pieces) > 1:
                pieces[0] = " ".join([*words[:i], pieces[0]])
                pieces[-1] = " ".join([pieces[-1], *words[i + 1 :]])
                return pieces
        return [name]

    def _split_word(self, word: str) -> list[str]:
        low = word.casefold()
        # Glued names are capitals run together ("MOSKOMILLEROADARU"); "Moonlight" / "Protokseed" are one word
        # (they came out as "Moon" + "light" on 114 events, Nov 2026).
        if len(low) < self.settings.lineup.min_glued_length or len(low) != len(word) or not word.isupper():
            return [word]
        for known in sorted(self._known, key=len, reverse=True):
            at = low.find(known) if len(known) < len(low) else -1  # a glued word was often recorded as known itself
            if at < 0:
                continue
            before, hit, after = word[:at], word[at : at + len(known)], word[at + len(known) :]
            if any(0 < len(side) < 3 for side in (before, after)):
                continue
            return [*(self._split_word(before) if before else []), hit, *(self._split_word(after) if after else [])]
        return [word]

    def _lineup_from_text(self, text: str | None) -> list[str]:
        if not text or self._lineup_header is None:
            return []
        lines = text.splitlines()
        names: list[str] = []
        i = 0
        while i < len(lines):
            header = _strip_decor(lines[i])
            if not (header and len(header.split()) <= 4 and self._is_lineup_header(lines[i], header)):
                i += 1
                continue
            # "LINEUP: A, B, C" on one line.
            _, _, inline = header.partition(":")
            names += [n for part in _LIST_SPLIT.split(inline) if (n := self._as_name(part))]
            i += 1
            collected = False
            while i < len(lines):
                candidate = _strip_decor(lines[i])
                if not candidate:
                    # Lists spaced by blank lines ("🔥 OSHI\n\n🔥 ORIGAMI") go on while the next line is a Latin name.
                    # A separator line ("~", "---") ends it: what follows is the next block ("Jeudi 12 novembre").
                    nxt = _strip_decor(lines[i + 1]) if i + 1 < len(lines) else ""
                    if collected and (lines[i].strip() or not (re.search(r"[a-z]", nxt, re.IGNORECASE) and self._as_name(nxt))):
                        break
                elif (
                    lines[i].strip().endswith(":")
                    or (
                        (candidate.startswith("(") and candidate.endswith(")"))  # "(MAINSTREAM, EDM, HARDSTYLE)"
                        or not _TBA.sub("", candidate).strip()  # "TBA LINE UP"
                        or candidate.casefold() in self._genre_words  # "HIP HOP" stage header
                        or self._not_a_name.search(candidate)  # "2ND STAGE – HIP HOP"
                    )
                    or not re.search(r"[^\W\d_]", candidate)
                ):  # stage / sub-header, e.g. "MAIN STAGE:"
                    collected = False
                elif line_names := self._line_names(candidate):
                    names += line_names
                    collected = True
                else:
                    break
                i += 1
        names += [
            f"DJ {m.group(1)}" for m in _INLINE_DJ.finditer(text) if m.group(1).split()[0].casefold() not in self._inline_dj_stop
        ]
        return names

    def _line_names(self, line: str) -> list[str]:
        """One lineup line -> names: "23:00 - 00:30 Cosmic Flow" (a timetable slot), "Remat Siaq · Tina · Orabi"
        and "Pretty Pink, Nat SuPrise, D´Joy und Anija" (a list on one line)."""
        line = _LEADING_WITH.sub("", _strip_decor(_SLOT.sub("", line)))  # "w/ SEAN ALONI" is not a list
        parts = [p for p in _LINE_SPLIT.split(line) if p.strip()]
        if len(parts) > 1 and re.search(r"\d", line):  # "4 quai Claude, 54000 Nancy", "10 € en prévente, 15 €"
            return []
        if len(parts) > 1:
            parts[-1:] = re.split(r"\s+(?:and|und|et|y)\s+", parts[-1], flags=re.IGNORECASE)
        names = [n for p in parts if (n := self._as_name(p))]
        if len(parts) > 1 and len(names) == len(parts):
            return names
        # Not a clean list ("TAL AVIV - מייסד ליין נויז, האיש ש...", "Nocapz (Hot Creations | House, Tech)"):
        # the line is one name with its note, as before.
        return [n] if (n := self._as_name(line)) else []

    def _is_lineup_header(self, raw: str, header: str) -> bool:
        """"LINE UP" / "🎧 הליינאפ" / "Lineup tonight:" - not a promo bullet like "🎶 ליינאפ מטורף"."""
        head = re.sub(r"\(.*?\)", " ", header.partition(":")[0].lower())  # "הליינאפ (לפי א״ב)"
        match = self._lineup_header.search(head) if self._lineup_header else None
        if match is None:
            return False
        rest = [w for w in (head[: match.start()] + " " + head[match.end() :]).split() if w not in self._header_filler]
        # Promo bullets are Hebrew prose ("ליינאפ מטורף"); "LINEUP — LIVE PERFORMANCES" is still a header.
        return not rest or (len(rest) <= 2 and all(w.isascii() for w in rest)) or raw.rstrip().endswith(":")

    def clean_name(self, value: str) -> str | None:
        """A stored lineup name under the current name rule: cleaned, or None (group_fill applies it to stored rows)."""
        return self._as_name(value)

    def _as_name(self, value: str) -> str | None:
        cfg = self.settings.lineup
        if "??" in value:  # "04] ??? — corrompido": a slot not announced yet
            return None
        name = _DJ_DASH.sub("DJ ", _LEADING_WITH.sub("", _strip_decor(_NUMBERING.sub("", _SLOT.sub("", value.strip())))))
        if re.match(r"[a-z]", name, re.IGNORECASE):
            name = _HEBREW.sub("", name)  # "YAARA RONמיינסטרים עד הבוקר" (source glued the next line)
        for pattern in (_ANNOTATION, _TBA, self._set_suffix):
            name = pattern.sub("", name)
        name = name.strip(" +-–—,.")
        if name and name[0] in "'\"" and name.count(name[0]) == 1:  # "'Seun Kuti & Egypt 80": a quote left open
            name = name[1:].strip()
        if (
            _NOT_A_NAME.search(name)
            or len(name) < 2
            or ":" in name  # "DRESS CODE: SHREK"
            or len(name) > cfg.max_name_length
            or len(name.split()) > cfg.max_name_words
            or (not re.search(r"[a-z]", name, re.IGNORECASE) and len(name.split()) > cfg.max_hebrew_name_words)
            or name.endswith(_SENTENCE_END)
            or "http" in name.lower()
            or re.search(r"\.(?:com|net|org|io|co|il)\b", name.lower())  # "soundcloud.com"
            or _DATE_OR_TIME.search(name)
            or re.search(r"\d{6,}|\w\.\w+/", name)  # a phone number, "shotgun.live/fr/venues/..."
            or re.search(r"\b(?:19|20)\d\d\b", name)  # "22 OCTOBER 2026"
            or self._not_a_name.search(name)
            or name.casefold() in self._lineup_ignore
            or name.casefold() in self._blocked
            or name.casefold() in self._genre_words  # "HIP HOP" / "MAINSTREAM" stage sub-headers
        ):
            return None
        return name

    # ------------------------------------------------------------------ tickets
    def _mark_free_tiers(self, event: ScrapedEvent) -> None:
        for tier in event.tickets:
            text = f"{tier.name} {tier.description or ''}".lower()
            says_free = bool(self._free and self._free.search(text))  # "חינם בכניסה עד 00:00" stays free
            if tier.price in (None, 0) and not says_free and self._pay_at_door and self._pay_at_door.search(text):
                tier.pay_at_door, tier.is_free = True, False
                tier.price = _amount(text)
            elif tier.price is None and says_free:
                tier.is_free = True
            elif tier.price is not None and not tier.price and not says_free and self._coupon and self._coupon.search(text):
                # "קוד למימוש מועדונים" at 0: redeems a club voucher, the price is not 0 for anyone else.
                tier.price, tier.is_free = None, False
        priced = [t.price for t in event.tickets if t.price is not None and t.is_active]
        if priced:
            event.min_price, event.max_price = min(priced), max(priced)

    @staticmethod
    def _entry_type(event: ScrapedEvent) -> EntryType:
        # A sold-out / closed tier is no longer an offer: "free until 00:00 — sold out" + 99 ₪ is a paid event.
        # Fully sold-out events keep their type (the sold_out flag covers them).
        tiers = [t for t in event.tickets if t.is_active] or event.tickets
        kinds = {t.kind for t in tiers if t.kind is not TicketKind.UNKNOWN}
        if not kinds and event.min_price is not None:
            kinds.add(TicketKind.FREE if event.min_price == 0 else TicketKind.PAID)
            if event.max_price is not None and event.max_price > 0:
                kinds.add(TicketKind.PAID)
        if kinds == {TicketKind.FREE}:
            return EntryType.FREE
        if kinds == {TicketKind.PAID}:
            return EntryType.PAID
        return EntryType.FREE_AND_PAID if kinds else EntryType.UNKNOWN

    # ------------------------------------------------------------------ time
    def _categorize_time(self, event: ScrapedEvent) -> None:
        if event.start_at is None:
            return
        if event.country and event.country != UNKNOWN_COUNTRY:
            from partylinks.scrapers.abroad import local_zone  # abroad imports this module

            # A zone the source gives (Posh: each venue's own) beats the country's guess.
            event.time_zone = event.time_zone or local_zone(event.country, self.tz.key, event.city)
        # A date alone is midnight in the zone the source meant: that day, and no time of day. Not
        # midnight there = the source did give an hour: a timed event, never an all-day one.
        if not event.time_known and event.start_at.timetz().replace(tzinfo=None) != time():
            event.time_known = True
        zone = ZoneInfo(event.time_zone) if event.time_zone else self.tz
        local = event.start_at.astimezone(zone) if event.time_known else event.start_at
        event.local_date = local.date()
        event.day_of_week = local.strftime("%A").lower()
        slots = self.settings.time_slots if event.time_known else ()
        event.time_slot = next((s.name for s in slots if s.contains(local.hour)), None)
        event.is_weekend = event.day_of_week in self._weekend

    # ------------------------------------------------------------------ city
    def canonical_city(self, city: str | None, address: str | None, venue: str | None = None) -> str | None:
        """One spelling per city ("Tel Aviv-Yafo", "תל אביב-יפו" -> "תל אביב"). The city field wins;
        otherwise the city named last in the address (streets are often named after cities:
        "שדרות ירושלים 19, תל אביב"), then in the venue name. Unknown cities are kept; zip codes dropped.
        A prefix ("קיבוץ יגור") goes, a whole Latin field naming a small town gets its Hebrew name."""
        if city and self._city_prefix:
            city = self._city_prefix.sub("", city.strip()) or city
        for text in (city, address, venue):
            if not text:
                continue
            hits = [(m.start(), name) for name, p in self._cities for m in p.finditer(text.lower())]
            if hits:
                return max(hits)[1]
        # Any other Israeli town (GeoNames), from the city field or the address only: a venue name
        # ("מגדל המים") is no town.
        for text in (city, address):
            if hits := self._places.find(text):
                return max(hits)[1]
        if city is None or city.strip().isdigit() or city.strip().casefold() in self._not_cities:
            return None
        if town := latin_town(city):  # "Ierusalim" -> the configured "ירושלים"; "kfar blum" -> "כפר בלום"
            return max(self._places.find(town[0]) or [(0, town[1])])[1]
        # One spelling of what is left: "קרית חיים" = "קריית חיים", "כרם מהר״ל" = "כרם מהר\"ל".
        return re.sub(r"^קרית ", "קריית ", city.strip().replace("״", '"').replace("׳", "'"))

    def in_target_city(self, city: str | None, address: str | None, venue: str | None) -> bool | None:
        """Whether the event is in the target city. A city or address that names another place means no;
        a bare venue name ("Sarona Park", "Ezra Bar") decides only when it names a street / area of the
        city (yes) or a place in another region (no), and is otherwise unknown (None)."""
        haystack = " | ".join(filter(None, [city, address, venue])).lower()
        if not haystack or self._city is None:
            return None
        # "יפו, ירושלים" is Jaffa Road: a city field naming another region decides before the address.
        if city and not self._city.search(city.lower()) and self.areas.named_region(city, None, None):
            return False
        if self._city.search(haystack):
            return True
        if city or address:
            return False
        if self.areas.keyword_area(venue, None):
            return True
        return False if self.areas.region(None, venue, None, None, None) != OUTSIDE_AREA else None


def _in_boxes(region: RegionSettings, latitude: float, longitude: float) -> bool:
    return any(a <= latitude <= b and c <= longitude <= d for a, b, c, d in region.boxes)


# Region place words that, inside a venue name or an address, are usually a street, a name or a direction.
NOT_A_PLACE_IN_NAMES = frozenset({"צפון", "הצפון", "גולן", "אילת", "שדרות", "אשכול", "גלילות"})


# Hebrew geresh / gershayim and curly quotes read as the ASCII ones: "ג׳אז", "ג’אז" and "ג'אז" are one word.
_QUOTES = str.maketrans({"׳": "'", "’": "'", "‘": "'", "`": "'", "״": '"', "“": '"', "”": '"'})


def _unquoted(words: list[str]) -> list[str]:
    return [w.translate(_QUOTES) for w in words]


# A word before "house" names a house sub-genre: "organic house", "chill house" are house only.
_HOUSE_NEXT = r"(?!\s+(?:house|האוס))"


def _genre_pattern(words: list[str]) -> re.Pattern[str] | None:
    """Genre keywords as _place_pattern; a keyword that is not a house one never matches before "house"."""
    plain = [w for w in words if "house" in w.lower() or "האוס" in w]
    guarded = [w for w in words if w not in plain]
    parts = [p.pattern for p in (_place_pattern(plain, 2), _place_pattern(guarded, 2, _HOUSE_NEXT)) if p]
    return re.compile("|".join(parts)) if parts else None


def _place_pattern(names: list[str], prefixes: int = 1, after: str = "") -> re.Pattern[str] | None:
    """Whole-word place names (and genre words). Hebrew names must not match inside other
    words ("יפו" inside "פטיפון" made a Rishon LeZion bar count as Tel Aviv, "טכנו" inside
    "טכנולוגיים" made a lecture techno), but may carry attached Hebrew prefix letters
    ("ביפו", "מתל אביב", "והטכנו")."""
    # One boundary around all the names of a kind, not one per name: a genre has dozens of words and
    # every event's text is searched for each genre.
    latin = "|".join(_spaced(n) for n in names if n.strip() and n.isascii())
    other = "|".join(_spaced(n) for n in names if n.strip() and not n.isascii())
    parts = [
        *([rf"(?<![\w])(?:{latin})(?![\w]){after}"] if latin else []),
        *([rf"(?<![\w])[ובהלמשכ]{{0,{prefixes}}}(?:{other})(?![\w]){after}"] if other else []),
    ]
    return re.compile("|".join(parts)) if parts else None


def _spaced(name: str) -> str:
    """A name's spaces and hyphens match a space, hyphen or maqaf: "hip hop" finds "hip-hop", "תל אביב" "תל-אביב"."""
    return re.sub(r"(?:\\ |\\-)+", lambda _: r"[\s\-־]+", re.escape(name.lower()))


def _free_entry_until(event: ScrapedEvent) -> datetime | None:
    """When free entry stops: the latest sale end of the active free tiers ("free until 23:30").

    None when any active free tier has no end (free all night) or none is known.
    """
    ends = [t.sale_end for t in event.tickets if t.kind is TicketKind.FREE and t.is_active]
    if not ends or any(end is None for end in ends):
        return None
    return max(e for e in ends if e is not None)
