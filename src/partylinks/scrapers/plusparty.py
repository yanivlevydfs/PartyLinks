"""Plus Party (plusparty.co.il, "מסיבות פלוס") - a guide to parties, shows and festivals across Israel.

robots.txt allows all but ``/admin``, ``/auth``, ``/account``, ``/nearby``, ``/api/`` and tracking
query strings; none of those is read. One request a run: ``/events`` is server-rendered (TanStack
Start) and embeds the query cache it was built from (``dehydratedData.queryClientState``, a JSON
string). Its ``events`` query is every upcoming event (~160; the site drops past ones itself), as
full rows: uuid, slug, title, description, kind (party / show / festival / wine), start and end in
UTC, venue, street, city, coordinates on a few, min/max price, sold-out status, minimum age,
organizer, performers and the poster. The page's JSON-LD ``ItemList`` holds the same events but
without the kind, and with "location to be announced" text in the city, so the state is read.
The sitemap's other ~160 event pages are past events and are not fetched.
What the site lacks: the seller's link. Its ticket button is gated behind a sign-up
(``data-ticket-gate``), and ``external_link`` is blank in the page, so no ``purchase_url``; the
description names the seller in passing ("כרטיסים ב-Zygo"). Nature parties hide the place until
tickets are bought ("לוקיישן בטבע (יפורסם לרוכשים)"): those get no venue.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Any

from pydantic import Field

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import ParseError, clean_str, parse_iso, to_decimal, to_float, to_int, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

# ponytail: retention's defaults (config/settings.yaml uses the same); pass Settings.retention in if they ever differ.
_RETENTION = RetentionSettings()
# The query cache the page hydrates from: a JS string literal holding JSON.
_STATE = re.compile(r'queryClientState:("(?:[^"\\]|\\.)*")')


class PlusPartySettings(SourceSettings):
    site_url: str = "https://plusparty.co.il"
    listing_path: str = "/events"
    event_path: str = "/events/{slug}"
    # The cached query whose rows are the events (its queryKey's first part).
    query_key: str = "events"
    # The site's kinds -> the names of its own category pages (/mesibot, /hofaot, /festivalim, /wine-festival).
    kinds: dict[str, str] = Field(
        default_factory=lambda: {"party": "מסיבה", "show": "הופעה", "festival": "פסטיבל", "wine": "פסטיבל יין"}
    )
    # Venue / place text that says the place is not published yet: no venue, and not an address.
    secret_place_words: list[str] = Field(
        default_factory=lambda: ["לוקיישן", "יפורסם", "שיפורסם", "יישלח", "יימסר", "מיקום סודי", "המיקום המדויק"]
    )
    sold_out_status: str = "sold_out"


class PlusPartyScraper(BaseScraper[PlusPartySettings]):
    name = "plusparty"
    settings_model = PlusPartySettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        rows = listing_rows(await self.get_text(s.site_url + s.listing_path), s)
        self.log.debug("listed %d events", len(rows))
        for row in rows:
            try:
                event = parse_event(row, s, self.timezone)
            except Exception as exc:  # noqa: BLE001 - one bad row must not cost the others
                self.record_failure(f"event {row.get('slug')}", exc)
                continue
            if event and is_current(event.start_at, event.end_at, _RETENTION, self.timezone):
                yield event


def listing_rows(page_html: str, s: PlusPartySettings) -> list[dict[str, Any]]:
    """The event rows of the page's cached ``events`` query."""
    match = _STATE.search(page_html)
    if not match:
        raise ParseError("listing has no queryClientState")
    state = json.loads(json.loads(match.group(1)))
    for query in state.get("queries") or []:
        key = query.get("queryKey") or []
        data = (query.get("state") or {}).get("data")
        if key and key[0] == s.query_key and isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
    raise ParseError(f"listing has no {s.query_key!r} query")


def _squash(text: str) -> str:
    """Letters only, without the vowel letters Hebrew spells either way ("קרית" / "קריית")."""
    return "".join(ch for ch in text if ch.isalnum() and ch not in "וי")


def _place(row: dict[str, Any], s: PlusPartySettings) -> tuple[str | None, str | None, str | None]:
    """venue, address, city: no venue that is a placeholder or only the town / area's name."""
    secret = lambda text: bool(text) and any(w in text for w in s.secret_place_words)
    city = clean_str(row.get("city"))
    venue = clean_str(row.get("venue_name"))
    street = clean_str(row.get("street_address"))
    location = clean_str(row.get("location"))  # "venue, street, city" in one line
    if street and city:
        address = street if _squash(city) in _squash(street) else f"{street}, {city}"
    else:  # no city field: the place line still names it ("אלטלנה 8, ראשון לציון")
        address = (None if secret(location) else location) or street
    # "כפר שיטים" at כפר שיטים, "עמק יזרעאל" whose whole place line is "עמק יזרעאל": a town or area, not a venue.
    if venue and (
        secret(venue)
        or (city and _squash(city).startswith(_squash(venue)))
        or (not city and not street and location and _squash(location) == _squash(venue))
    ):
        venue = None
    return venue, address, None if secret(city) else city


def parse_event(row: dict[str, Any], s: PlusPartySettings, timezone: str) -> ScrapedEvent | None:
    """One cached event row -> event; None without id, slug, title or date, or when not published / cancelled."""
    title, slug = clean_str(row.get("title")), clean_str(row.get("slug"))
    if not row.get("id") or not slug or not title or row.get("published") is False:
        return None
    if str(row.get("event_status") or "scheduled") == "cancelled":
        return None
    tz = zone(timezone)
    start = parse_iso(row.get("event_date"))
    if start is None:
        return None
    end = parse_iso(row.get("end_at"))
    venue, address, city = _place(row, s)
    lat, lng = to_float(row.get("latitude")), to_float(row.get("longitude"))
    kind = str(row.get("kind") or "")
    low = to_decimal(row.get("price_min"))
    if low is None:
        low = to_decimal(row.get("price"))
    high = to_decimal(row.get("price_max")) or low
    images = [u for u in (clean_str(row.get(k)) for k in ("image_url", "og_image")) if u and u.startswith("http")]
    return ScrapedEvent(
        source=PlusPartyScraper.name,
        source_event_id=str(row["id"]),
        url=s.site_url + s.event_path.format(slug=slug),
        title=title,
        description=clean_str(row.get("description")),
        start_at=start.astimezone(tz),
        end_at=end.astimezone(tz) if end and end > start else None,
        time_known=True,
        venue_name=venue,
        address=address,
        city=city,
        latitude=lat if lat and lng else None,
        longitude=lng if lat and lng else None,
        source_genres=[s.kinds.get(kind, kind).lower()] if kind else [],
        artists=[n for n in (clean_str(p) for p in row.get("performers") or [] if isinstance(p, str)) if n],
        organizer=clean_str(row.get("organizer_name")),
        min_age=to_int(row.get("age_limit")),
        images=list(dict.fromkeys(images)),
        currency=clean_str(row.get("currency")) or "ILS",
        min_price=low,
        max_price=high if high is not None and (low is None or high >= low) else low,
        is_sold_out=row.get("ticket_status") == s.sold_out_status,
    )
