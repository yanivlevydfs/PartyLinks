"""EfoKulam (efokulam.co.il, "איפה כולם") - a nightlife guide: club nights, parties and festivals, mostly Tel Aviv.

A WordPress site whose ``events`` post type is public in wp-json; this source reads it the way the page's
own REST links do:
- ``/wp-json/wp/v2/event-type`` (one request): the site's music categories (טכנו, מיינסטרים, היפ הופ...),
  id -> name, kept as source genres. About half the events carry none; those get ``base_genre`` (a party).
- ``/wp-json/wp/v2/events`` in pages of 100 with the featured image embedded (~5 requests, ~460 rows):
  ``meta`` gives the date (``event-date``: the local wall clock written as a UTC epoch, so 23:00 reads
  23:00 UTC), the start (``event-gate_open``), a Google-formatted address and the seller's link
  (``ticket_link``: Go-Out, Eventer...), kept as ``extra["purchase_url"]`` so dedup merges the event with
  that seller's own listing. REST has no date filter, so past rows are dropped from the listing by their
  ``event-date`` and ``is_current`` (~50 current events); no event page is fetched.
The site has no venue field and no price (its "החל מ-₪0" chip is the free-entry-before-midnight line): the
venue is the address's first part only when the address reads "<place>, <street>, <city>"; else the
venue pass names it from its spot. The address is geocoded (no coordinates on the site).
robots.txt disallows only /wp-admin/.
"""

from __future__ import annotations

import html
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from pydantic import Field

from partylinks.config import RetentionSettings
from partylinks.countries import country_code
from partylinks.enrichment import UNKNOWN_COUNTRY
from partylinks.models import ScrapedEvent
from partylinks.parsing import ParseError, clean_str, html_to_text, to_int, zone
from partylinks.retention import is_current
from partylinks.scrapers.abroad import local_zone
from partylinks.scrapers.base import BaseScraper, SourceSettings

# ponytail: retention's defaults (config/settings.yaml uses the same); pass Settings.retention in if they ever differ.
_RETENTION = RetentionSettings()
_CLOCK = re.compile(r"^(\d{1,2}):(\d{2})")


class EfoKulamSettings(SourceSettings):
    site_url: str = "https://efokulam.co.il"
    events_api_path: str = "/wp-json/wp/v2/events"
    types_api_path: str = "/wp-json/wp/v2/event-type"
    per_page: int = 100  # the REST API maximum
    max_pages: int = Field(50, ge=1)
    # Events without a category on a nightlife guide are club nights: this declared genre maps to nightclubs.
    base_genre: str = "מסיבה"
    # Title prefixes left over from the seller's "duplicate event" button.
    title_prefixes: list[str] = Field(default_factory=lambda: ["Copy of "])
    # Address parts that are a street, a PO box or a region, never a venue name.
    street_words: list[str] = Field(
        default_factory=lambda: ["רחוב", "רח'", "שדרות", "שד'", "דרך", "כיכר", "ת.ד", "מחוז", "צומת"]
    )
    po_box: str = "ת.ד"


class EfoKulamScraper(BaseScraper[EfoKulamSettings]):
    name = "efokulam"
    settings_model = EfoKulamSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        terms = await self.get_json(
            s.site_url + s.types_api_path, params={"per_page": s.per_page, "_fields": "id,name"}
        )
        types = {t["id"]: html.unescape(str(t["name"])) for t in terms if isinstance(t, dict) and t.get("id")}

        async def page(index: int) -> tuple[list[dict[str, Any]], str | None]:
            params = {"per_page": s.per_page, "page": index + 1, "_embed": "wp:featuredmedia",
                      "_fields": "id,link,title,content,meta,event-type,_links,_embedded"}  # fmt: skip
            response = await self.request("GET", s.site_url + s.events_api_path, params=params)
            rows = response.json()
            if not isinstance(rows, list):
                raise ParseError(f"{response.request.url}: expected a list of events")
            # The total is required: asking past the last page is an error, not an empty page.
            return [r for r in rows if isinstance(r, dict)], response.headers.get("X-WP-Total")

        rows = await self.paginate(page, s.per_page, s.max_pages)
        self.log.debug("listed %d events", len(rows))
        for row in rows:
            try:
                event = parse_event(row, types, s, self.timezone)
            except Exception as exc:  # noqa: BLE001 - one bad row must not cost the others
                self.record_failure(f"event {row.get('id')}", exc)
                continue
            if event and is_current(
                event.start_at, event.end_at, _RETENTION, self.timezone, time_known=event.time_known
            ):
                yield event


def _is_city(venue: str, city: str) -> bool:
    """A "venue" that is only the town's name ("תל אביב" before "תל אביב-יפו")."""
    squash = lambda text: "".join(ch for ch in text if ch.isalnum() and ch not in "וי")
    return squash(city).startswith(squash(venue))


def split_address(address: str | None, s: EfoKulamSettings) -> tuple[str | None, str | None, str | None]:
    """(venue, address without the PO box, ISO country when abroad) of a Google-formatted address."""
    parts = [p.strip() for p in (address or "").split(",") if p.strip() and not p.strip().startswith(s.po_box)]
    if not parts:
        return None, None, None
    code = country_code(parts[-1])
    local = parts[:-1] if code != UNKNOWN_COUNTRY else parts
    first = local[0].split("|")[0].strip() if local else ""
    # "<place>, <street>, <city>" only: "הלוחמים, תל אביב-יפו" is a street, "האנגר 23" an address.
    venue = (
        first
        if len(local) >= 3
        and not any(ch.isdigit() for ch in first)
        and not any(first.startswith(w) for w in s.street_words)
        and not _is_city(first, local[-1])
        else None
    )
    return venue, ", ".join(parts), code if code not in (UNKNOWN_COUNTRY, "IL") else None


def _title(row: dict[str, Any], s: EfoKulamSettings) -> str | None:
    title = clean_str(html.unescape(str((row.get("title") or {}).get("rendered") or "")))
    for prefix in s.title_prefixes:
        if title and title.lower().startswith(prefix.lower()):
            title = clean_str(title[len(prefix) :])
    return title


def _image(row: dict[str, Any]) -> list[str]:
    media = (row.get("_embedded") or {}).get("wp:featuredmedia") or []
    url = clean_str(media[0].get("source_url")) if media and isinstance(media[0], dict) else None
    return [url] if url and url.startswith("http") else []


def parse_event(row: dict[str, Any], types: dict[int, str], s: EfoKulamSettings, timezone: str) -> ScrapedEvent | None:
    """One REST row -> event; None without an id, title or date."""
    meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    title = _title(row, s)
    stamp = to_int(meta.get("event-date"))
    if not row.get("id") or not title or not stamp:
        return None
    venue, address, country = split_address(clean_str(meta.get("address")), s)
    tz_name = local_zone(country, timezone) if country else timezone
    tz = zone(tz_name)
    # The epoch is the local wall clock read as UTC: same digits, the venue's zone.
    start = datetime.fromtimestamp(stamp, UTC).replace(tzinfo=tz)
    clock = _CLOCK.match(clean_str(meta.get("event-gate_open")) or "")
    if clock:
        start = start.replace(hour=int(clock.group(1)) % 24, minute=int(clock.group(2)))
    genres = [types[t] for t in row.get("event-type") or [] if t in types] or [s.base_genre]
    link = clean_str(meta.get("ticket_link"))
    return ScrapedEvent(
        source=EfoKulamScraper.name,
        source_event_id=str(row["id"]),
        url=str(row.get("link") or ""),
        title=title,
        description=html_to_text(meta.get("short_description"))
        or html_to_text((row.get("content") or {}).get("rendered")),
        start_at=start,
        time_known=clock is not None,
        venue_name=venue,
        address=address,
        country=country,
        time_zone=tz_name if country else None,
        source_genres=list(dict.fromkeys(g.lower() for g in genres)),
        images=_image(row),
        extra={"purchase_url": link} if link and link.startswith("http") else {},
    )
