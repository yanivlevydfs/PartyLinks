"""Post-scrape normalization and categorization.

Adds canonical genres, the DJ/performer lineup, local date / weekday / time slot,
free-vs-paid entry, target-city matching and the area of the city. It runs on finished
``ScrapedEvent`` objects only, so it never affects how any source is fetched.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from partylinks.config import AreaSettings, EnrichmentSettings, RegionSettings
from partylinks.models import EntryType, ScrapedEvent, TicketKind
from partylinks.parsing import slugify

_EDGE_DECOR = re.compile(r"^[^\w'\"(]+|[^\w'\")]+$")
_LEADING_WITH = re.compile(r"^(?:w/|with|feat\.?|ft\.?)\s+", re.IGNORECASE)
_DATE_OR_TIME = re.compile(r"\d{1,2}\s*[./:]\s*\d{1,2}")
_LIST_SPLIT = re.compile(r"\s*(?:,|\||•|/|·)\s*")
# "DJ NAME" written inline in running text: upper-case words on the same line ("DJ Mag" / "DJ Night" are not names).
_DJ_WORD = r"[A-ZÀ-Þ0-9](?:[\w&'’-]|\.(?=\w))*"  # "MR.BLACK" keeps its dot; "Mag." ends the name
_INLINE_DJ = re.compile(rf"\bDJ[^\S\n]+({_DJ_WORD}(?:[^\S\n]+{_DJ_WORD}){{0,2}})")
# Back-to-back sets are separate artists: "LANDAU B2B ANSTY", "NYx × Ansty".
_COLLAB = re.compile(r"\s+(?:b2b|f2f|vs\.?|x|×)\s+|\s+b2b(?=[A-Z])", re.IGNORECASE)
# Notes after a name: "rotemx {Q}", "Ravid Polianski (Birthday)", "distørpya {GERMANY" (closer already stripped).
_ANNOTATION = re.compile(r"\s*[(\[{].*$")
_TBA = re.compile(r"(?:\s+and)?\s*more\s+tba\b.*$|\s*\b(?:tba|t\.b\.a\.?)(?:\s.*)?$", re.IGNORECASE)
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


OUTSIDE_AREA = "outside"
ABROAD_AREA = "abroad"
# ISO 3166 "unknown": an event abroad whose country the source does not give. Any country code
# keeps an event out of the Israeli feed and in the abroad section.
UNKNOWN_COUNTRY = "ZZ"


def meters(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Equirectangular distance: plenty accurate inside one city."""
    lat = math.radians((a[0] + b[0]) / 2)
    dx = math.radians(b[1] - a[1]) * math.cos(lat)
    dy = math.radians(b[0] - a[0])
    return 6_371_000 * math.hypot(dx, dy)


class AreaMatcher:
    """Area of the target city, or region of the country outside it, for an event
    (also used to backfill stored rows)."""

    def __init__(self, areas: list[AreaSettings], regions: list[RegionSettings] | None = None) -> None:
        self._areas = [(area, _keyword_pattern(area.keywords)) for area in areas]
        self._regions = [(r, _place_pattern(r.places)) for r in regions or []]
        self.region_keys = {r.key for r in regions or []} | {ABROAD_AREA, OUTSIDE_AREA}

    def match(
        self,
        venue_name: str | None,
        address: str | None,
        latitude: float | None,
        longitude: float | None,
        in_target_city: bool | None,
        city: str | None = None,
    ) -> str | None:
        if not self._areas:
            return None
        if in_target_city is False:
            return self.region(city, venue_name, address, latitude, longitude)
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
        latitude: float | None, longitude: float | None,
    ) -> str:
        """Region of an event outside the target city: a named place (city first), then coordinates."""
        if named := self.named_region(city, venue_name, address):
            return named.key
        if latitude is None or longitude is None or not self._regions:
            return OUTSIDE_AREA
        for region, _ in self._regions:
            if _in_boxes(region, latitude, longitude):
                return region.key
        return ABROAD_AREA

    def named_region(self, city: str | None, venue_name: str | None, address: str | None) -> RegionSettings | None:
        """Region of the first place named in the city, address or venue name (in that order)."""
        for text in (city, address, venue_name):
            for region, pattern in self._regions:
                if text and pattern and pattern.search(text.lower()):
                    return region
        return None

    def contradicts(
        self, city: str | None, venue_name: str | None, address: str | None, latitude: float, longitude: float
    ) -> bool:
        """True when the text names a region whose boxes miss the point ("קיבוץ הגושרים" placed in the Negev)."""
        named = self.named_region(city, venue_name, address)
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
            genre: pattern for genre, words in settings.genres.items() if (pattern := _place_pattern(words, prefixes=2))
        }
        self._talk = _place_pattern(settings.talk_keywords, prefixes=2)
        self._free = _keyword_pattern(settings.free_entry_keywords)
        self._pay_at_door = _keyword_pattern(settings.pay_at_door_keywords)
        self._city = _place_pattern(settings.target_city.aliases)
        self._cities = [(c.he, p) for c in settings.cities if (p := _place_pattern([c.he, c.en, *c.aliases]))]
        self._lineup_header = _keyword_pattern(settings.lineup.header_keywords)
        self._lineup_ignore = {w.casefold() for w in settings.lineup.ignore}
        self._genre_words = {w.casefold() for words in settings.genres.values() for w in words}
        lineup = settings.lineup
        self._header_filler = {w.casefold() for w in lineup.header_filler}
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
        self.areas = AreaMatcher(settings.areas, settings.regions)

    def is_dropped(self, event: ScrapedEvent) -> bool:
        """True when enrich() would drop the event (another city while focusing on one)."""
        return self.settings.target_city.drop_other_cities and self.in_target_city(event.city, event.address, event.venue_name) is False

    def enrich(self, event: ScrapedEvent) -> ScrapedEvent | None:
        """Return the enriched event, or ``None`` if config says to drop it."""
        self._slugs(event)
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
        self._categorize_time(event)
        event.city = self.canonical_city(event.city, event.address, event.venue_name)
        event.in_target_city = self.in_target_city(event.city, event.address, event.venue_name)
        event.area = self.areas.match(
            event.venue_name, event.address, event.latitude, event.longitude, event.in_target_city, event.city
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
        declared = " | ".join([*event.source_genres, *event.event_types]).lower()
        talk = self._talk is not None and self._talk.search(event.title.lower())
        text = f"{event.title}\n{event.description or ''}".lower() if self.settings.detect_genres_from_text and not talk else ""
        return sorted(
            genre for genre, pattern in self._genres.items() if pattern.search(declared) or pattern.search(text)
        )

    # ------------------------------------------------------------------ lineup
    def _lineup(self, event: ScrapedEvent) -> list[str]:
        names = [*event.artists, *self._lineup_from_text(event.description), *self._featured_in(event)]
        venue = (event.venue_name or "").casefold()
        unique: dict[str, str] = {}
        for raw in names:
            for part in (p for piece in _COLLAB.split(raw) for p in self._unglue(piece)):
                name = self._as_name(part)
                if name is not None and name.casefold() not in venue:  # "Haoman 17" is the venue, not a DJ
                    unique.setdefault(name.casefold(), name)
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
        return [spelling[m.group(0).casefold()] for m in pattern.finditer(text)]

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
        if len(low) < self.settings.lineup.min_glued_length or len(low) != len(word):
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
                    nxt = _strip_decor(lines[i + 1]) if i + 1 < len(lines) else ""
                    if collected and not (re.search(r"[a-z]", nxt, re.IGNORECASE) and self._as_name(nxt)):
                        break
                elif lines[i].strip().endswith(":") or (
                    (candidate.startswith("(") and candidate.endswith(")"))  # "(MAINSTREAM, EDM, HARDSTYLE)"
                    or not _TBA.sub("", candidate).strip()  # "TBA LINE UP"
                    or candidate.casefold() in self._genre_words  # "HIP HOP" stage header
                    or self._not_a_name.search(candidate)  # "2ND STAGE – HIP HOP"
                ):  # stage / sub-header, e.g. "MAIN STAGE:"
                    collected = False
                elif name := self._as_name(candidate):
                    names.append(name)
                    collected = True
                else:
                    break
                i += 1
        names += [
            f"DJ {m.group(1)}" for m in _INLINE_DJ.finditer(text) if m.group(1).split()[0].casefold() not in self._inline_dj_stop
        ]
        return names

    def _is_lineup_header(self, raw: str, header: str) -> bool:
        """"LINE UP" / "🎧 הליינאפ" / "Lineup tonight:" - not a promo bullet like "🎶 ליינאפ מטורף"."""
        head = re.sub(r"\(.*?\)", " ", header.partition(":")[0].lower())  # "הליינאפ (לפי א״ב)"
        match = self._lineup_header.search(head) if self._lineup_header else None
        if match is None:
            return False
        rest = [w for w in (head[: match.start()] + " " + head[match.end() :]).split() if w not in self._header_filler]
        # Promo bullets are Hebrew prose ("ליינאפ מטורף"); "LINEUP — LIVE PERFORMANCES" is still a header.
        return not rest or (len(rest) <= 2 and all(w.isascii() for w in rest)) or raw.rstrip().endswith(":")

    def _as_name(self, value: str) -> str | None:
        cfg = self.settings.lineup
        name = _DJ_DASH.sub("DJ ", _LEADING_WITH.sub("", _strip_decor(value)))
        if re.match(r"[a-z]", name, re.IGNORECASE):
            name = _HEBREW.sub("", name)  # "YAARA RONמיינסטרים עד הבוקר" (source glued the next line)
        for pattern in (_ANNOTATION, _TBA, self._set_suffix):
            name = pattern.sub("", name)
        name = name.strip(" +-–—,.")
        if (
            len(name) < 2
            or ":" in name  # "DRESS CODE: SHREK"
            or len(name) > cfg.max_name_length
            or len(name.split()) > cfg.max_name_words
            or (not re.search(r"[a-z]", name, re.IGNORECASE) and len(name.split()) > cfg.max_hebrew_name_words)
            or name.endswith(_SENTENCE_END)
            or "http" in name.lower()
            or _DATE_OR_TIME.search(name)
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
        local = event.start_at.astimezone(self.tz)
        event.local_date = local.date()
        event.day_of_week = local.strftime("%A").lower()
        event.time_slot = next((s.name for s in self.settings.time_slots if s.contains(local.hour)), None)
        event.is_weekend = event.day_of_week in self._weekend

    # ------------------------------------------------------------------ city
    def canonical_city(self, city: str | None, address: str | None, venue: str | None = None) -> str | None:
        """One spelling per city ("Tel Aviv-Yafo", "תל אביב-יפו" -> "תל אביב"). The city field wins;
        otherwise the city named last in the address (streets are often named after cities:
        "שדרות ירושלים 19, תל אביב"), then in the venue name. Unknown cities are kept; zip codes dropped."""
        for text in (city, address, venue):
            if not text:
                continue
            hits = [(m.start(), name) for name, p in self._cities for m in p.finditer(text.lower())]
            if hits:
                return max(hits)[1]
        return None if city is None or city.strip().isdigit() else city.strip()

    def in_target_city(self, city: str | None, address: str | None, venue: str | None) -> bool | None:
        """Whether the event is in the target city. A city or address that names another place means no;
        a bare venue name ("Sarona Park", "Ezra Bar") decides only when it names a street / area of the
        city (yes) or a place in another region (no), and is otherwise unknown (None)."""
        haystack = " | ".join(filter(None, [city, address, venue])).lower()
        if not haystack or self._city is None:
            return None
        if self._city.search(haystack):
            return True
        if city or address:
            return False
        if self.areas.keyword_area(venue, None):
            return True
        return False if self.areas.region(None, venue, None, None, None) != OUTSIDE_AREA else None


def _in_boxes(region: RegionSettings, latitude: float, longitude: float) -> bool:
    return any(a <= latitude <= b and c <= longitude <= d for a, b, c, d in region.boxes)


def _place_pattern(names: list[str], prefixes: int = 1) -> re.Pattern[str] | None:
    """Whole-word place names (and genre words). Hebrew names must not match inside other
    words ("יפו" inside "פטיפון" made a Rishon LeZion bar count as Tel Aviv, "טכנו" inside
    "טכנולוגיים" made a lecture techno), but may carry attached Hebrew prefix letters
    ("ביפו", "מתל אביב", "והטכנו")."""
    parts = [
        rf"(?<![\w]){re.escape(n.lower())}(?![\w])" if n.isascii()
        else rf"(?<![\w])[ובהלמשכ]{{0,{prefixes}}}{re.escape(n.lower())}(?![\w])"
        for n in names
        if n.strip()
    ]
    return re.compile("|".join(parts)) if parts else None


def _free_entry_until(event: ScrapedEvent) -> datetime | None:
    """When free entry stops: the latest sale end of the active free tiers ("free until 23:30").

    None when any active free tier has no end (free all night) or none is known.
    """
    ends = [t.sale_end for t in event.tickets if t.kind is TicketKind.FREE and t.is_active]
    if not ends or any(end is None for end in ends):
        return None
    return max(e for e in ends if e is not None)
