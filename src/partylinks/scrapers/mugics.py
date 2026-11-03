"""Mugics (mugics.com) - an Israeli live-music board: shows musicians and venues post themselves, with a map.

The site's own pages read their pins from its API host (``d.mugics.com``); this source reads it the same way:
- ``POST /api/map-pins`` with ``type: liveMusic`` and a ``begins`` / ``ends`` window gives every live show
  in that window as full rows (name, description, date + start time, coordinates, street, city, venue
  name, price, artwork, the seller's ticket link, the site's category and up to two music styles).
- The call returns at most 200 pins, so the horizon is read in 30-day windows from today (no past show
  is fetched), and a window that hits the cap is halved until it fits: ~25 requests a run, ~390 shows.
- ``/api/pins/<page>`` (the paged list) pages by counts of pins already seen per subscription tier and
  repeats rows, so it is not used.
robots.txt on www.mugics.com disallows ``/api/`` (the Next.js routes there); the owner chose to read the
API host anyway (Nov 2026). Most shows are listed by venues we also read (Zappa, Gray, Shablul,
Smarticket halls): ``ticketLink`` is kept as ``extra["purchase_url"]`` so dedup merges them.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import quote, urlsplit

from pydantic import Field

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier
from partylinks.parsing import ParseError, clean_str, parse_iso, to_decimal, to_float, to_int, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

# ponytail: retention's defaults (config/settings.yaml uses the same); pass Settings.retention in if they ever differ.
_RETENTION = RetentionSettings()
# The characters JavaScript's encodeURIComponent leaves as they are (the site builds its pin links with it).
_URI_SAFE = "-_.!~*'()"


class MugicsSettings(SourceSettings):
    site_url: str = "https://mugics.com"
    api_url: str = "https://d.mugics.com/api/map-pins"
    pin_path: str = "/he/pin/{id}/{slug}"
    pin_type: str = "liveMusic"
    window_days: int = 30
    horizon_days: int = 730
    # map-pins answers at most this many pins; a window that returns as many is split in two.
    page_cap: int = 200
    min_window_hours: int = 12
    # Countries the site writes for Israel; a pin elsewhere is skipped (the site is Israeli, times are Israel's).
    israel_names: list[str] = Field(default_factory=lambda: ["ישראל", "israel", "il"])
    # The site's category ids -> its own Hebrew names (kept as source genres).
    categories: dict[str, str] = Field(
        default_factory=lambda: {
            "musicclub": "מועדון הופעות",
            "intimatespace": "הופעות אינטימיות",
            "festival": "פסטיבלים והופעות ענק",
            "openmic": "במות פתוחות",
            "street": "הופעות רחוב",
            "singersongwriter": "אומן יוצר",
        }
    )
    # Every pin is a live show: this genre is added to the site's own ones.
    base_genre: str = "הופעה חיה"
    free_ticket_name: str = "כניסה חופשית"
    # The site's place field is free text: spellings of one hall -> the name its own box office uses,
    # so the venue gets one page (keys lower case).
    venue_names: dict[str, str] = Field(
        default_factory=lambda: {
            "צוללת צהובה": "הצוללת הצהובה",
            "חלוץ": "החלוץ 33",
            "גריי ביהוד": "גריי יהוד",
            "gray יהוד": "גריי יהוד",
            "וונדרבר חיפה": "וונדרבר",
            "תחנת רוח קריית טבעון": "תחנת רוח",
            "בית ההופעות של חנן | אקספו תל אביב בלקבוקס": "בית ההופעות של חנן | אקספו תל אביב BLACK BOX",
        }
    )
    # Ticket links that are a profile, not a ticket page: not kept as the purchase link.
    social_hosts: list[str] = Field(
        default_factory=lambda: ["instagram.com", "facebook.com", "fb.me", "linktr.ee", "wa.me", "t.me", "youtube.com"]
    )


class MugicsScraper(BaseScraper[MugicsSettings]):
    name = "mugics"
    settings_model = MugicsSettings

    def default_headers(self) -> dict[str, str]:
        return super().default_headers() | {"Origin": "https://www.mugics.com", "Referer": "https://www.mugics.com/"}

    async def _window(self, begins: datetime, ends: datetime) -> list[dict[str, Any]]:
        s = self.settings
        body = {
            "type": s.pin_type,
            "begins": _iso(begins),
            "ends": _iso(ends),
            "category": "",
            "subCategory": "",
            "location": None,
            "mapLocation": None,
            "iWeAre": "",
            "lookingForType": None,
            "isMobile": False,
        }
        data = await self.post_json(s.api_url, json=body)
        pins = data.get("pins") if isinstance(data, dict) else None
        if isinstance(pins, dict):  # grouped by street on some answers
            pins = [p for group in pins.values() if isinstance(group, list) for p in group]
        if not isinstance(pins, list):
            raise ParseError(f"map-pins: no pin list in {str(data)[:120]}")
        rows = [p for p in pins if isinstance(p, dict)]
        if len(rows) >= s.page_cap and ends - begins > timedelta(hours=s.min_window_hours):
            middle = begins + (ends - begins) / 2
            return await self._window(begins, middle) + await self._window(middle, ends)
        return rows

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        tz = zone(self.timezone)
        start = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
        rows: dict[str, dict[str, Any]] = {}
        for offset in range(0, s.horizon_days, s.window_days):
            begins = start + timedelta(days=offset)
            for row in await self._window(begins, begins + timedelta(days=s.window_days)):
                if row.get("_id"):
                    rows[str(row["_id"])] = row
        self.log.debug("listed %d pins", len(rows))
        for row in rows.values():
            try:
                event = parse_event(row, s, self.timezone)
            except Exception as exc:  # noqa: BLE001 - one bad row must not cost the others
                self.record_failure(f"pin {row.get('_id')}", exc)
                continue
            if event and is_current(
                event.start_at, event.end_at, _RETENTION, self.timezone, time_known=event.time_known
            ):
                yield event


def _iso(moment: datetime) -> str:
    return moment.astimezone(zone("UTC")).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def pin_url(row: dict[str, Any], s: MugicsSettings) -> str:
    """The site's own pin link: id and name, whitespace and slashes in the name turned into dashes."""
    name = "-".join(str(row.get("name") or "").strip().replace("\\", " ").replace("/", " ").split()) or "pin"
    return s.site_url + s.pin_path.format(id=quote(str(row["_id"]), safe=_URI_SAFE), slug=quote(name, safe=_URI_SAFE))


def _start(row: dict[str, Any], timezone: str) -> tuple[datetime | None, bool]:
    """``date`` is the show's local midnight in UTC; ``time`` the seconds after it (None = no time given)."""
    day = parse_iso(row.get("date"))
    if day is None:
        return None, False
    local = day.astimezone(zone(timezone)).replace(hour=0, minute=0, second=0, microsecond=0)
    seconds = to_int(row.get("time"))
    if seconds is None or not 0 <= seconds < 86400:
        return local, False
    return local + timedelta(seconds=seconds), True


def _purchase_url(link: Any, s: MugicsSettings) -> str | None:
    link = clean_str(link)
    if not link:
        return None
    if not link.startswith("http"):
        link = "https://" + link
    host = (urlsplit(link).hostname or "").removeprefix("www.")
    if not host or any(host == h or host.endswith("." + h) for h in s.social_hosts):
        return None
    return link


def _prices(row: dict[str, Any], s: MugicsSettings) -> tuple[Decimal | None, Decimal | None, list[TicketTier]]:
    prices = row.get("prices") if isinstance(row.get("prices"), dict) else {}
    kind = prices.get("priceType")
    if kind == "free":
        return Decimal(0), Decimal(0), [TicketTier(name=s.free_ticket_name, price=Decimal(0), is_free=True)]
    if kind == "price":
        price = to_decimal(prices.get("price"))
        return (price, price, []) if price else (None, None, [])
    if kind == "price-range":
        low, high = to_decimal(prices.get("min")), to_decimal(prices.get("max"))
        low = low or None
        high = high if high and (low is None or high >= low) else low
        return low, high, []
    return None, None, []  # "hat" (pay what you like) or unknown


def _is_city(venue: str | None, city: str | None) -> bool:
    """A "venue" that is only the town's name (the site's free-text place field: "יהוד", "תלאביב")."""
    if not venue:
        return True
    # Letters only, without the vowel letters Hebrew spells either way ("קרית" / "קריית").
    squash = lambda text: "".join(ch for ch in text if ch.isalnum() and ch not in "וי")
    return bool(city) and squash(city).startswith(squash(venue))


def parse_event(row: dict[str, Any], s: MugicsSettings, timezone: str) -> ScrapedEvent | None:
    """One map pin -> event; None for another pin type, no id / name / date, or a pin abroad."""
    title = clean_str(row.get("name"))
    if row.get("type") != s.pin_type or not row.get("_id") or not title:
        return None
    start, time_known = _start(row, timezone)
    if start is None:
        return None
    location = row.get("location") if isinstance(row.get("location"), dict) else {}
    country = (clean_str(location.get("country")) or "ישראל").lower()
    if country not in s.israel_names:
        return None
    position = location.get("position") if isinstance(location.get("position"), dict) else {}
    lat, lng = to_float(position.get("lat")), to_float(position.get("lng"))
    city = clean_str(location.get("city"))
    street = clean_str(location.get("street"))
    number = to_int(location.get("number"))
    street_line = f"{street} {number}" if street and number else street
    address = ", ".join(p for p in (street_line, city) if p) or None
    venue = clean_str(location.get("revenueName"))
    venue = s.venue_names.get(venue.lower(), venue) if venue else None
    category = s.categories.get(str(row.get("category") or ""))
    genres = [s.base_genre, *([category] if category else [])]
    genres += [g for g in (clean_str(x) for x in row.get("subCategories") or []) if g]
    images = [
        src
        for img in row.get("images") or []
        if isinstance(img, dict) and (src := clean_str(img.get("src"))) and src.startswith("http")
    ]
    low, high, tickets = _prices(row, s)
    return ScrapedEvent(
        source=MugicsScraper.name,
        source_event_id=str(row["_id"]),
        url=pin_url(row, s),
        title=title,
        description=clean_str(row.get("desc")),
        start_at=start,
        time_known=time_known,
        venue_name=None if _is_city(venue, city) else venue,
        address=address,
        city=city,
        latitude=lat if lat and lng else None,
        longitude=lng if lat and lng else None,
        source_genres=list(dict.fromkeys(g.lower() for g in genres)),
        images=images,
        min_price=low,
        max_price=high,
        tickets=tickets,
        extra={"purchase_url": _purchase_url(row.get("ticketLink"), s), "repeats_weekly": bool(row.get("isRepeat"))},
    )
