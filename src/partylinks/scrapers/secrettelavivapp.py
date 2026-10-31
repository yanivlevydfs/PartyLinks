"""Secret Tel Aviv App (secrettelaviv.app) - the Secret Tel Aviv team's newer city guide (Base44 app).

Event pages are an empty JavaScript shell; the page itself loads its data from the app's public Base44
entities API (``/api/apps/<app id>/entities/<Entity>``), which this source reads the same way:
- ``Event`` filtered server-side to ``calculated_status`` upcoming / live and an end date from today on,
  so no past event is fetched
  (~300 rows, one request): names and descriptions in English / Hebrew, start / end date and time,
  the original event / seller link (``event_link``, kept as ``extra["purchase_url"]``), artwork,
  category ids, coordinates and the id of the venue's ``Location``.
- ``Location`` for those ids (one request): venue name and Hebrew address.
- ``EventCategory`` (one request): category id -> English name, the same names as secrettelaviv.com,
  so the ``enrichment.genres`` keywords already map them.
The site shows no price. Events the .com feed also lists are merged by dedup (same seller link and time).
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from pydantic import Field

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent
from partylinks.parsing import clean_str, to_float, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

# ponytail: retention's defaults (config/settings.yaml uses the same); pass Settings.retention in if they ever differ.
_RETENTION = RetentionSettings()
_HEBREW = re.compile(r"[֐-׿]")
_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_CLOCK = re.compile(r"(\d{1,2}):(\d{2})")


class SecretTelAvivAppSettings(SourceSettings):
    site_url: str = "https://secrettelaviv.app"
    app_id: str = "698edf27551cfa67a4553353"
    entities_path: str = "/api/apps/{app_id}/entities/{entity}"
    event_path: str = "/event/{slug}"
    live_statuses: list[str] = Field(default_factory=lambda: ["upcoming", "live"])
    page_limit: int = 5000
    # End time the site fills in when the organiser gave none ("until late"): dropped.
    placeholder_end_time: str = "23:55"
    # Event links that are a profile, not a ticket page: not kept as the purchase link.
    social_hosts: list[str] = Field(
        default_factory=lambda: ["instagram.com", "facebook.com", "fb.me", "linktr.ee", "wa.me", "t.me"]
    )


class SecretTelAvivAppScraper(BaseScraper[SecretTelAvivAppSettings]):
    name = "secrettelavivapp"
    settings_model = SecretTelAvivAppSettings

    def default_headers(self) -> dict[str, str]:
        return super().default_headers() | {"X-App-Id": self.settings.app_id}

    async def _entities(self, entity: str, query: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        s = self.settings
        params: dict[str, Any] = {"limit": s.page_limit}
        if query:
            params["q"] = json.dumps(query)
        url = s.site_url + s.entities_path.format(app_id=s.app_id, entity=entity)
        rows = await self.get_json(url, params=params)
        return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        # The site's calculated_status lags (an event over yesterday may still read "upcoming"), so its end date
        # is filtered too, and every event goes through retention's own rule (is_current) before it is yielded.
        today = datetime.now(zone(self.timezone)).date().isoformat()
        query = {"status": "active", "calculated_status": {"$in": s.live_statuses}, "event_end_date": {"$gte": today}}
        rows = await self._entities("Event", query)
        self.log.info("listed %d events", len(rows))
        location_ids = sorted({r["base44_location_id"] for r in rows if r.get("base44_location_id")})
        locations = (
            await self.optional("locations", self._entities("Location", {"id": {"$in": location_ids}}))
            if location_ids
            else []
        )
        categories = await self.optional("categories", self._entities("EventCategory")) or []
        context = Lookups(
            locations={r["id"]: r for r in locations or [] if r.get("id")},
            categories={
                r["id"]: n for r in categories if r.get("id") and (n := clean_str(r.get("event_category_name")))
            },
        )
        for row in rows:
            try:
                event = parse_event(row, context, s, self.timezone)
            except Exception as exc:  # noqa: BLE001 - one bad row must not cost the others
                self.record_failure(f"event {row.get('id')}", exc)
                continue
            if event and is_current(event.start_at, event.end_at, _RETENTION, self.timezone, time_known=event.time_known):
                yield event


class Lookups:
    def __init__(self, locations: dict[str, dict[str, Any]], categories: dict[str, str]) -> None:
        self.locations = locations
        self.categories = categories


def _when(day: Any, clock: Any, timezone: str) -> datetime | None:
    d = _DATE.fullmatch(str(day or "").strip())
    if not d:
        return None
    c = _CLOCK.fullmatch(str(clock or "").strip())
    h, m = (int(c[1]), int(c[2])) if c else (0, 0)
    return datetime(int(d[1]), int(d[2]), int(d[3]), h % 24, m, tzinfo=zone(timezone))


def _pick(row: dict[str, Any], hebrew: str, english: str) -> str | None:
    """The Hebrew text when it is written in Hebrew, else the English one."""
    he = clean_str(row.get(hebrew))
    return he if he and _HEBREW.search(he) else clean_str(row.get(english)) or he


def _purchase_url(link: Any, s: SecretTelAvivAppSettings) -> str | None:
    link = clean_str(link)
    if not link or not link.startswith("http"):
        return None
    host = (urlsplit(link).hostname or "").removeprefix("www.")
    return None if any(host == h or host.endswith("." + h) for h in s.social_hosts) else link


def parse_event(
    row: dict[str, Any], lookups: Lookups, s: SecretTelAvivAppSettings, timezone: str
) -> ScrapedEvent | None:
    """One ``Event`` row -> event; None without a slug, a title or a start date."""
    slug = clean_str(row.get("slug"))
    title = _pick(row, "event_name_hebrew", "event_name")
    start = _when(row.get("event_start_date"), row.get("event_start_time"), timezone)
    if not slug or not title or not start or row.get("is_sample"):
        return None
    end_clock = clean_str(row.get("event_end_time"))
    end = None
    if end_clock and end_clock != s.placeholder_end_time:
        end = _when(row.get("event_end_date") or row.get("event_start_date"), end_clock, timezone)
    location = lookups.locations.get(row.get("base44_location_id") or "") or {}
    venue = clean_str(location.get("restaurant_name")) or _pick(row, "new_location_name_hebrew", "new_location_name")
    address = (
        clean_str(location.get("location_address_hebrew"))
        or clean_str(location.get("location_address"))
        or _pick(row, "new_location_address_hebrew", "new_location_address")
    )
    lat = (
        to_float(row.get("event_lat"))
        or to_float(row.get("new_location_lat"))
        or to_float(location.get("location_lat"))
    )
    lng = (
        to_float(row.get("event_lng"))
        or to_float(row.get("new_location_lng"))
        or to_float(location.get("location_long"))
    )
    image = clean_str(row.get("image_url"))
    return ScrapedEvent(
        source=SecretTelAvivAppScraper.name,
        source_event_id=str(row["id"]),
        url=s.site_url + s.event_path.format(slug=slug),
        title=title,
        description=_pick(row, "description_he", "description_en") or _pick(row, "event_summary_he", "event_summary"),
        start_at=start,
        end_at=end if end and end > start else None,
        time_known=bool(_CLOCK.fullmatch(str(row.get("event_start_time") or "").strip())),
        venue_name=venue,
        address=address,
        latitude=lat if lat and lng else None,
        longitude=lng if lat and lng else None,
        source_genres=[n.lower() for c in row.get("event_categories") or [] if (n := lookups.categories.get(c))],
        images=[image] if image and image.startswith("http") else [],
        extra={"purchase_url": _purchase_url(row.get("event_link"), s), "title_en": clean_str(row.get("event_name"))},
    )
