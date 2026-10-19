"""Post-scrape normalization and categorization.

Adds canonical genres, the DJ/performer lineup, local date / weekday / time slot,
free-vs-paid entry and target-city matching. It runs on finished
``ScrapedEvent`` objects only, so it never affects how any source is fetched.
"""

from __future__ import annotations

import hashlib
import re
from decimal import Decimal
from zoneinfo import ZoneInfo

from partylinks.config import EnrichmentSettings
from partylinks.models import EntryType, ScrapedEvent, TicketKind
from partylinks.parsing import slugify

_EDGE_DECOR = re.compile(r"^[^\w'\"(]+|[^\w'\")]+$")
_LEADING_WITH = re.compile(r"^(?:w/|with|feat\.?|ft\.?)\s+", re.IGNORECASE)
_DATE_OR_TIME = re.compile(r"\d{1,2}\s*[./:]\s*\d{1,2}")
_LIST_SPLIT = re.compile(r"\s*(?:,|\||•|/|·)\s*")
# "DJ NAME" written inline in running text (names are capitalized words).
_INLINE_DJ = re.compile(r"\bDJ\s+([A-Z0-9][\w.&'’-]*(?:\s+[A-Z0-9][\w.&'’-]*){0,2})")
_SENTENCE_END = (".", "!", "?", "…")


def _keyword_pattern(keywords: list[str]) -> re.Pattern[str] | None:
    """ASCII keywords match on word boundaries; Hebrew ones as substrings
    (Hebrew attaches prefixes like ה/ב/ל directly to words)."""
    parts = [
        rf"(?<![a-z0-9]){re.escape(k.lower())}(?![a-z0-9])" if k.isascii() else re.escape(k.lower())
        for k in keywords
        if k.strip()
    ]
    return re.compile("|".join(parts)) if parts else None


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
            genre: pattern for genre, words in settings.genres.items() if (pattern := _keyword_pattern(words))
        }
        self._free = _keyword_pattern(settings.free_entry_keywords)
        self._pay_at_door = _keyword_pattern(settings.pay_at_door_keywords)
        self._city = _keyword_pattern(settings.target_city.aliases)
        self._lineup_header = _keyword_pattern(settings.lineup.header_keywords)
        self._lineup_ignore = {w.casefold() for w in settings.lineup.ignore}
        self._weekend = {d.lower() for d in settings.weekend_days}

    def enrich(self, event: ScrapedEvent) -> ScrapedEvent | None:
        """Return the enriched event, or ``None`` if config says to drop it."""
        self._slugs(event)
        event.genres = self._detect_genres(event)
        event.lineup = self._lineup(event)
        self._mark_free_tiers(event)
        event.entry_type = self._entry_type(event)
        event.has_free_entry = event.entry_type in (EntryType.FREE, EntryType.FREE_AND_PAID)
        self._categorize_time(event)
        event.in_target_city = self._in_target_city(event)
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
        text = f"{event.title}\n{event.description or ''}".lower() if self.settings.detect_genres_from_text else ""
        return sorted(
            genre for genre, pattern in self._genres.items() if pattern.search(declared) or pattern.search(text)
        )

    # ------------------------------------------------------------------ lineup
    def _lineup(self, event: ScrapedEvent) -> list[str]:
        names = [*event.artists, *self._lineup_from_text(event.description)]
        unique: dict[str, str] = {}
        for name in names:
            key = name.casefold()
            if key not in self._lineup_ignore:
                unique.setdefault(key, name)
        return list(unique.values())[: self.settings.lineup.max_names]

    def _lineup_from_text(self, text: str | None) -> list[str]:
        if not text or self._lineup_header is None:
            return []
        lines = text.splitlines()
        names: list[str] = []
        i = 0
        while i < len(lines):
            header = _strip_decor(lines[i])
            if not (header and len(header.split()) <= 4 and self._lineup_header.search(header.lower())):
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
                    if collected:
                        break
                elif lines[i].strip().endswith(":"):  # stage / sub-header, e.g. "MAIN STAGE:"
                    collected = False
                elif name := self._as_name(candidate):
                    names.append(name)
                    collected = True
                else:
                    break
                i += 1
        names += [f"DJ {m.group(1)}" for m in _INLINE_DJ.finditer(text)]
        return names

    def _as_name(self, value: str) -> str | None:
        cfg = self.settings.lineup
        name = _LEADING_WITH.sub("", _strip_decor(value)).strip(" +-–—")
        if (
            not name
            or len(name) > cfg.max_name_length
            or len(name.split()) > cfg.max_name_words
            or name.endswith(_SENTENCE_END)
            or "http" in name.lower()
            or _DATE_OR_TIME.search(name)
            or name.casefold() in self._lineup_ignore
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
        kinds = {t.kind for t in event.tickets if t.kind is not TicketKind.UNKNOWN}
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
    def _in_target_city(self, event: ScrapedEvent) -> bool | None:
        haystack = " | ".join(filter(None, [event.city, event.address, event.venue_name])).lower()
        if not haystack or self._city is None:
            return None
        return bool(self._city.search(haystack))
