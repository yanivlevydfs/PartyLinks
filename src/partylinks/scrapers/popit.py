"""Popit (popit.events) - party board that aggregates events from ticketing sites.

robots.txt disallows ``/api``, so the JSON API is not used. Listing: the public
``/explore/all`` page (Next.js App Router) embeds every listed event object in its
React Server Components payload (``self.__next_f.push``); one request returns the
whole catalogue, so there is no pagination and no per-event request. Long texts
(the bio) are referenced as ``"$<id>"`` and live in separate ``<id>:T<hexlen>,``
text rows of the same payload.

Popit only links out: there are no ticket tiers or prices, only the seller's URL
(kept as ``extra["purchase_url"]`` so dedup links it to the seller's own event).
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import httpx

from partylinks.models import ScrapedEvent
from partylinks.parsing import (
    ParseError,
    clean_str,
    from_unix,
    html_to_text,
    to_float,
    to_int,
    zone,
)
from partylinks.scrapers.base import BaseScraper, SourceSettings

_PUSH = "self.__next_f.push("
_EVENT_START = re.compile(r'\{"id":"\d+"')
_REF = re.compile(r"\$[0-9a-f]+")
_MIN_AGE = re.compile(r"גילאי\s*(\d+)")
_COUNTRY = {"ישראל", "Israel"}


class PopitSettings(SourceSettings):
    site_url: str
    listing_path: str
    event_page_path: str


class PopitScraper(BaseScraper[PopitSettings]):
    name = "popit"
    settings_model = PopitSettings

    def is_blocked(self, response: httpx.Response) -> bool:
        return response.headers.get("cf-mitigated") == "challenge"

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        rsc = flight_payload(await self.get_text(s.site_url + s.listing_path))
        events = event_objects(rsc)
        if not events:
            raise ParseError("no event objects in the listing page payload")
        self.log.info("listed %d events", len(events))
        for raw in events:
            try:
                yield parse_event(raw, rsc, s.site_url + s.event_page_path, self.timezone)
            except Exception as exc:  # noqa: BLE001 - one bad item never drops the rest
                self.record_failure(f"event {raw.get('id')}", exc)


def flight_payload(page_html: str) -> str:
    """The concatenated RSC text of a Next.js App Router page."""
    decoder = json.JSONDecoder()
    parts: list[str] = []
    i = page_html.find(_PUSH)
    while i >= 0:
        try:
            chunk, _ = decoder.raw_decode(page_html, i + len(_PUSH))
        except ValueError:
            chunk = None
        if isinstance(chunk, list) and len(chunk) == 2 and chunk[0] == 1 and isinstance(chunk[1], str):
            parts.append(chunk[1])
        i = page_html.find(_PUSH, i + 1)
    return "".join(parts)


def event_objects(rsc: str) -> list[dict[str, Any]]:
    """Every distinct event object (identified by its ``eventTimeMs`` key) in the payload."""
    decoder = json.JSONDecoder()
    found: dict[str, dict[str, Any]] = {}
    for match in _EVENT_START.finditer(rsc):
        try:
            obj, _ = decoder.raw_decode(rsc, match.start())
        except ValueError:
            continue
        if isinstance(obj, dict) and "eventTimeMs" in obj:
            found.setdefault(obj["id"], obj)
    return list(found.values())


def _value(rsc: str, value: Any) -> Any:
    """Resolve RSC placeholders: ``$undefined`` -> None, ``$<id>`` -> that text row."""
    if not isinstance(value, str) or not value.startswith("$"):
        return value
    if not _REF.fullmatch(value):
        return None
    row = re.search(rf"(?<![0-9a-f]){re.escape(value[1:])}:T([0-9a-f]+),", rsc)
    if row is None:
        return None
    size = int(row.group(1), 16)  # byte length of the UTF-8 text
    return rsc[row.end() : row.end() + size].encode()[:size].decode("utf-8", errors="ignore")


def _local(date: Any, time: Any, timezone: str) -> datetime | None:
    try:
        return datetime.strptime(f"{date} {time}", "%d-%m-%y %H:%M").replace(tzinfo=zone(timezone))
    except (TypeError, ValueError):
        return None


def parse_event(raw: dict[str, Any], rsc: str, page_url_template: str, timezone: str) -> ScrapedEvent:
    event = {key: _value(rsc, value) for key, value in raw.items()}
    if not event.get("id") or not clean_str(event.get("name")):
        raise ParseError("event id/name missing")

    tz = zone(timezone)
    start = from_unix(event.get("eventTimeMs"))
    start = start.astimezone(tz) if start else _local(event.get("event_date"), event.get("time"), timezone)
    end = _local(event.get("event_end_date"), event.get("event_end_time"), timezone)
    if end is not None and (start is None or end <= start):
        end = None  # the source sometimes reports an end before the start

    precise = event.get("location_type") == "precise"
    lon, lat = (event.get("location") or [None, None])[:2] if precise else (None, None)
    parts = [p.strip() for p in (clean_str(event.get("formatted_address")) or "").split(",") if p.strip()]
    if parts and parts[-1] in _COUNTRY:
        parts.pop()
    keywords = [k for k in event.get("keywords") or [] if isinstance(k, str)]
    age = next((m for m in map(_MIN_AGE.search, keywords) if m), None)

    return ScrapedEvent(
        source=PopitScraper.name,
        source_event_id=str(event["id"]),
        url=page_url_template.format(event_id=event["id"]),
        title=clean_str(event["name"]) or "",
        description=html_to_text(event.get("bio")) or clean_str(event.get("seoMetaDescription")),
        start_at=start,
        end_at=end,
        venue_name=clean_str(event.get("canonicalVenueName")) or (clean_str(event.get("locationName")) if precise else None),
        address=", ".join(parts) if precise and parts else None,
        city=parts[-1] if parts else None,
        latitude=to_float(lat),
        longitude=to_float(lon),
        source_genres=[g for g in event.get("genres") or [] if g],
        artists=[a for a in (clean_str(d) for d in event.get("djs") or []) if a],
        min_age=to_int(age.group(1)) if age else None,
        organizer=clean_str(event.get("producer_name")) or clean_str(event.get("organizer")),
        image_url=clean_str(event.get("image")),
        extra={
            "purchase_url": clean_str(event.get("url")),
            "origin_source": event.get("source"),
            "keywords": keywords,
            "experience": event.get("experience"),
            "location_type": event.get("location_type"),
            "availability": event.get("availabilityStatus"),
            "discount_label": event.get("discountLabel"),
            "discount_code": event.get("discountCode"),
            "featured": event.get("featured"),
        },
    )
