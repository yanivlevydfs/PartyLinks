"""KLIKA (klika.live) - an Israeli ticketing app for party lines ("clickas"), built on Supabase.

Its pages are an empty JavaScript shell; they read the app's public Supabase API with the anon key the
page itself ships, and this source does the same:
- ``GET /rest/v1/events``: every public, published, non-test event from today on (row-level security
  returns only public events, the listed ones and those only their line's page shows). One request:
  name, slug, date, start / end clock, venue name and street address, description, the line it belongs to.
- ``POST /rest/v1/rpc/public_seo_catalog``: the lines ("ecosystems") with their own genre names, the
  only categories the site has. One request.
- ``POST /rest/v1/rpc/public_ticket_page_v2`` per event: the ticket tiers (price, state: available /
  sold_out / pending), the hero artwork and the lineup (DJ names, b2b partners).
The public event page is ``/t/<slug>``; it is also where tickets are bought. The site has no
coordinates and no city field: the address (which names the city) is geocoded.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from pydantic import Field

from partylinks.config import RetentionSettings
from partylinks.models import ScrapedEvent, TicketTier, TierStatus
from partylinks.parsing import clean_str, read_clock, to_decimal, zone
from partylinks.retention import is_current
from partylinks.scrapers.base import BaseScraper, SourceSettings

# ponytail: retention's defaults (config/settings.yaml uses the same); pass Settings.retention in if they ever differ.
_RETENTION = RetentionSettings()


class KlikaSettings(SourceSettings):
    site_url: str = "https://klika.live"
    api_url: str = "https://gypklkntkzgvjtqffdvb.supabase.co/rest/v1"
    # The public anon key klika.live's own bundle sends (role "anon"); rotate here if the site changes it.
    anon_key: str = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imd5cGtsa250a3pndmp0cWZmZHZiIiwicm9sZSI6"
        "ImFub24iLCJpYXQiOjE3NzM0MDc1MDAsImV4cCI6MjA4ODk4MzUwMH0.pqquv6tlIci7_MMZzzFyAy1h_dtqF3TQiQaTuJ8TtJo"
    )
    events_columns: str = (
        "id,slug,name,event_date,event_start,event_end,doors_open,venue_name,venue_address,description,"
        "ecosystem_id,registration_only,members_only"
    )
    catalog_rpc: str = "public_seo_catalog"
    ticket_page_rpc: str = "public_ticket_page_v2"
    event_path: str = "/t/{slug}"
    # Tier states on the ticket page -> our tier status.
    tier_states: dict[str, str] = Field(
        default_factory=lambda: {
            "available": TierStatus.ON_SALE,
            "sold_out": TierStatus.SOLD_OUT,
            "pending": TierStatus.COMING_SOON,
            "closed": TierStatus.CLOSED,
            "expired": TierStatus.CLOSED,
        }
    )
    registration_tier_name: str = "הרשמה"


class KlikaScraper(BaseScraper[KlikaSettings]):
    name = "klika"
    settings_model = KlikaSettings

    def default_headers(self) -> dict[str, str]:
        key = self.settings.anon_key
        return super().default_headers() | {"apikey": key, "Authorization": f"Bearer {key}"}

    async def _rpc(self, name: str, body: dict[str, Any]) -> Any:
        return await self.post_json(f"{self.settings.api_url}/rpc/{name}", json=body)

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        s = self.settings
        today = datetime.now(zone(self.timezone)).date().isoformat()
        params = {
            "select": s.events_columns,
            "event_date": f"gte.{today}",
            "is_test": "is.false",
            "status": "eq.published",
            "visibility": "eq.public",
            "deleted_at": "is.null",
            "hidden_at": "is.null",
            "order": "event_date",
        }
        rows = await self.get_json(f"{s.api_url}/events", params=params, expect=list)
        self.log.debug("listed %d events", len(rows))
        catalog = await self.optional("catalog", self._rpc(s.catalog_rpc, {})) or {}
        lines = {e["id"]: e for e in catalog.get("ecosystems") or [] if isinstance(e, dict) and e.get("id")}

        async def one(row: dict[str, Any]) -> ScrapedEvent | None:
            page = await self.optional(
                f"ticket page {row.get('slug')}", self._rpc(s.ticket_page_rpc, {"_key": row["slug"], "_promo": None})
            )
            event = parse_event(row, page or {}, lines.get(row.get("ecosystem_id") or "") or {}, s, self.timezone)
            if event and is_current(
                event.start_at, event.end_at, _RETENTION, self.timezone, time_known=event.time_known
            ):
                return event
            return None

        rows = [r for r in rows if isinstance(r, dict) and r.get("slug")]
        async for event in self.stream_isolated(rows, one, lambda r: f"event {r.get('slug')}"):
            yield event


def _at(day: datetime, clock: Any, timezone: str) -> datetime | None:
    hm = read_clock(clock)
    return day.replace(hour=hm.hour, minute=hm.minute, tzinfo=zone(timezone)) if hm else None


def parse_tiers(tiers: list[dict[str, Any]], s: KlikaSettings) -> list[TicketTier]:
    out = []
    for t in tiers or []:
        name = clean_str(t.get("name"))
        price = to_decimal(t.get("price"))
        if not name or price is None:
            continue
        status = TierStatus(s.tier_states.get(str(t.get("state") or ""), TierStatus.ON_SALE))
        out.append(
            TicketTier(
                source_ticket_id=clean_str(t.get("id")),
                name=name,
                description=clean_str(t.get("description")),
                price=price,
                is_free=price == 0,
                is_sold_out=status == TierStatus.SOLD_OUT,
                is_active=status == TierStatus.ON_SALE,
                status=status,
            )
        )
    return out


def parse_artists(lineup: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for slot in lineup or []:
        for key in ("dj_name", "b2b_partner_name"):
            if (n := clean_str(slot.get(key))) and n not in names:
                names.append(n)
    return names


def parse_event(
    row: dict[str, Any], page: dict[str, Any], line: dict[str, Any], s: KlikaSettings, timezone: str
) -> ScrapedEvent | None:
    """One ``events`` row (+ its ticket page and line) -> event; None without a slug, a name or a date."""
    slug, title = clean_str(row.get("slug")), clean_str(row.get("name"))
    day_text = clean_str(row.get("event_date")) or ""
    if not slug or not title or len(day_text) < 10:
        return None
    # event_date carries the local calendar day (stored at midnight UTC); the clock is in event_start.
    day = datetime.fromisoformat(day_text[:10])
    start = _at(day, row.get("event_start") or row.get("doors_open"), timezone)
    time_known = start is not None
    start = start or day.replace(tzinfo=zone(timezone))
    end = _at(day, row.get("event_end"), timezone) if time_known else None
    if end and end <= start:
        end += timedelta(days=1)  # ends after midnight
    tiers = parse_tiers(page.get("tiers") or [], s)
    if not tiers and row.get("registration_only"):
        tiers = [TicketTier(name=s.registration_tier_name, price=Decimal(0), is_free=True)]
    lineup = page.get("lineup") or []
    genres = [g.lower() for g in line.get("genres") or [] if isinstance(g, str)]
    genres += [g.lower() for x in lineup if (g := clean_str(x.get("genre"))) and g.lower() not in genres]
    image = clean_str(page.get("hero_image"))
    url = s.site_url + s.event_path.format(slug=slug)
    return ScrapedEvent(
        source=KlikaScraper.name,
        source_event_id=str(row["id"]),
        url=url,
        title=title,
        description=clean_str(row.get("description")),
        start_at=start,
        end_at=end,
        time_known=time_known,
        venue_name=clean_str(row.get("venue_name")),
        address=clean_str(row.get("venue_address")),
        source_genres=genres,
        artists=parse_artists(lineup),
        organizer=clean_str(line.get("name")),
        images=[image] if image and image.startswith("http") else [],
        tickets=tiers,
        extra={"purchase_url": url, "members_only": bool(row.get("members_only"))},
    )
