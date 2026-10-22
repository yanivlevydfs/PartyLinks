"""Smarticket - white-label box office used by theatres and municipal culture halls.

Every venue runs its own tenant host (``<name>.smarticket.co.il`` or its own domain).
Listing: one ``GET https://<host>/api/shows`` per tenant returns every show with its
title, category, image (a file under ``/uploads/``), description and ``events[]``:
one entry per performance with date, start/end time, hall (``event_place``),
availability flags, an ``unavailability_msg`` and the full ``pricelist``, so no
detail requests are needed. The public page of a performance is ``/event/<id>``.

Some tenants (Petah Tikva) answer ``/api/shows`` with ``[]`` although they sell
shows; for those the calendar feed ``/api/show_theater/get_events_calendar?start&end``
is read in 6-month windows instead. It has no category, hall or description, and
lists member/code-only price rows, which are dropped.

An unknown tenant answers 200 with a small "לא נמצאה מערכת" HTML page: that tenant
is recorded as a config error and the others still run. Tenant hosts have no
robots.txt, but the vendor's main site disallows ``/api/``, so keep this gentle:
one request per tenant per run (plus the calendar windows for an empty tenant), low concurrency (``http.concurrency``).
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from partylinks.models import ScrapedEvent, TicketTier, TierStatus
from partylinks.parsing import ParseError, clean_str, html_to_text, to_decimal, to_int
from partylinks.scrapers.base import BaseScraper, SourceSettings

UNKNOWN_TENANT = "לא נמצאה מערכת"
# Free-text reasons a venue gives when a performance cannot be booked online.
_CANCELLED = re.compile(r"לא\s+תתקיים|בוטל|נדחה")
_SOLD_OUT = re.compile(r"אזל")
_SOON = re.compile(r"בקרוב|טרם\s+נפתח|תהיה\s+זמינה")
_FREE = re.compile(r"הכניסה\s+חופשית|ללא\s+עלות")
CALENDAR_WINDOW = timedelta(days=182)


class SmarticketTenant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Short name (expands to <host>.<tenant_domain>) or a full host when it contains a dot.
    host: str
    # Used when a performance has no hall (``event_place``).
    venue_name: str | None = None
    city: str | None = None
    # The halls are rooms of this one venue ("אולם 1", "במה ראשית"): the venue is venue_name.
    rooms: bool = False
    # Shows whose category contains one of these are not events (cinema screenings, zoo entry).
    skip_categories: list[str] = Field(default_factory=list)
    # Shows titled exactly this are not events (a cinema's unnamed "קופה ראשית" slots).
    skip_titles: list[str] = Field(default_factory=list)
    # Category of shows the tenant leaves uncategorized (a cinematheque: "קולנוע").
    category: str | None = None


class SmarticketSettings(SourceSettings):
    tenant_domain: str
    shows_path: str
    event_page_path: str
    uploads_path: str
    calendar_path: str
    # 6-month calendar windows read for a tenant whose /api/shows is empty.
    calendar_windows: int = Field(2, ge=1)
    tenants: list[SmarticketTenant]


def tenant_host(name: str, domain: str) -> str:
    return name if "." in name else f"{name}.{domain}"


class SmarticketScraper(BaseScraper[SmarticketSettings]):
    name = "smarticket"
    settings_model = SmarticketSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        async for events in self.stream_isolated(self.settings.tenants, self._scrape_tenant, label=lambda t: t.host):
            for event in events:
                yield event

    async def _scrape_tenant(self, tenant: SmarticketTenant) -> list[ScrapedEvent]:
        s = self.settings
        host = tenant_host(tenant.host, s.tenant_domain)
        body = await self.get_text(f"https://{host}{s.shows_path}")
        shows = parse_shows_body(body, host) or await self._calendar_shows(host)
        shows = [sh for sh in shows if not any(w in str(sh.get("category") or "") for w in tenant.skip_categories)]
        shows = [sh for sh in shows if clean_str(sh.get("title")) not in tenant.skip_titles]
        events: list[ScrapedEvent] = []
        now = datetime.now(ZoneInfo(self.timezone))
        for show, performance in iter_performances(shows):
            try:
                event = parse_event(show, performance, tenant, s, self.timezone)
            except Exception as exc:  # noqa: BLE001 - one bad performance never drops the tenant
                self.record_failure(f"{tenant.host}:{performance.get('id')}", exc)
                continue
            if event is not None and event.start_at is not None and (event.end_at or event.start_at) >= now:
                events.append(event)
        self.log.info("%s: %d upcoming events", tenant.host, len(events))
        return events

    async def _calendar_shows(self, host: str) -> list[dict[str, Any]]:
        start, rows = date.today(), []
        for _ in range(self.settings.calendar_windows):
            end = start + CALENDAR_WINDOW
            payload = await self.get_json(
                f"https://{host}{self.settings.calendar_path}",
                params={"start": start.isoformat(), "end": end.isoformat()},
                expect=dict,
            )
            rows += [r for r in payload.get("result") or [] if isinstance(r, dict)]
            start = end + timedelta(days=1)
        return calendar_shows(rows)


def calendar_shows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Calendar rows in the /api/shows shape (one show per performance), deduplicated by id."""
    shows: dict[Any, dict[str, Any]] = {}
    for row in rows:
        if row.get("id") is None:
            continue
        performance = {
            "id": row["id"],
            "show_date": row.get("start_date"),
            "show_time": row.get("start_time"),
            "end_time": row.get("end_time"),
            "availability": bool(to_int(row.get("website_available"))),
            "tickets_available": to_int(row.get("website_left_tickets_count")) != 0,
            "pricelist": [
                {"id": p.get("ticket_type"), "name": p.get("ticket_type_name"), "price": p.get("price")}
                for p in row.get("website_pricelist") or []
                if isinstance(p, dict) and p.get("condition") == "none"
            ],
        }
        shows[row["id"]] = {"title": row.get("name"), "image": row.get("image"), "events": [performance]}
    return list(shows.values())


def parse_shows_body(body: str, host: str) -> list[dict[str, Any]]:
    if UNKNOWN_TENANT in body:
        raise ParseError(f"{host}: no such Smarticket tenant (fix sources.smarticket.tenants)")
    try:
        shows = json.loads(body)
    except ValueError as exc:
        raise ParseError(f"{host}: /api/shows did not return JSON") from exc
    if not isinstance(shows, list):
        raise ParseError(f"{host}: expected a list of shows, got {type(shows).__name__}")
    return [show for show in shows if isinstance(show, dict)]


def iter_performances(
    shows: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    return [(show, p) for show in shows for p in show.get("events") or [] if isinstance(p, dict) and p.get("id")]


def parse_event(
    show: dict[str, Any],
    performance: dict[str, Any],
    tenant: SmarticketTenant,
    s: SmarticketSettings,
    timezone: str,
) -> ScrapedEvent | None:
    """One performance; None when the venue marks it cancelled or gives it no date."""
    message = clean_str(performance.get("unavailability_msg"))
    if message and _CANCELLED.search(message):
        return None
    start = _local(performance.get("show_date"), performance.get("show_time"), timezone)
    if start is None:
        return None
    end = _local(performance.get("show_date"), performance.get("end_time"), timezone)
    if end is not None and end <= start:
        end += timedelta(days=1)  # ends after midnight

    status = _status(performance, message)
    tickets = [
        TicketTier(
            source_ticket_id=str(p["id"]) if p.get("id") is not None else None,
            name=clean_str(p.get("name")) or "כרטיס",
            price=(price := to_decimal(p.get("price"))),
            is_free=price == 0,
            status=status,
        )
        for p in performance.get("pricelist") or []
        if isinstance(p, dict)
    ]
    if not tickets and message and _FREE.search(message):
        tickets = [TicketTier(name="כניסה חופשית", price=to_decimal(0), is_free=True, status=status)]
    prices = [t.price for t in tickets if t.price is not None]

    host = tenant_host(tenant.host, s.tenant_domain)
    category = clean_str(show.get("category")) or tenant.category
    image = clean_str(show.get("image"))
    return ScrapedEvent(
        source=SmarticketScraper.name,
        source_event_id=f"{tenant.host}:{performance['id']}",
        url=f"https://{host}{clean_str(performance.get('permalink')) or s.event_page_path.format(id=performance['id'])}",
        title=clean_str(performance.get("title")) or clean_str(show.get("title")) or "",
        description=html_to_text(performance.get("content") or show.get("content") or show.get("brief")),
        start_at=start,
        end_at=end,
        venue_name=(None if tenant.rooms else clean_str(performance.get("event_place"))) or tenant.venue_name,
        city=tenant.city,
        source_genres=[category] if category else [],
        images=[f"https://{host}{s.uploads_path}{image}"] if image else [],
        min_price=min(prices, default=None),
        max_price=max(prices, default=None),
        is_sold_out=status is TierStatus.SOLD_OUT,
        tickets=tickets,
        extra={
            "tenant": tenant.host,
            "show_id": to_int(show.get("id")),
            "hall": clean_str(performance.get("event_place")),
            "duration_minutes": to_int(performance.get("duration")),
            "unavailability_msg": message,
        },
    )


def _status(performance: dict[str, Any], message: str | None) -> TierStatus:
    """``tickets_available: false`` means sold out only while the performance is bookable here;
    unbookable ones (``availability: false``, often with an external link as the message) are
    sold elsewhere or at the door."""
    available = performance.get("availability") is not False
    if (message and _SOLD_OUT.search(message)) or (available and performance.get("tickets_available") is False):
        return TierStatus.SOLD_OUT
    if not available:
        return TierStatus.COMING_SOON if message and _SOON.search(message) else TierStatus.CLOSED
    return TierStatus.ON_SALE


def _local(day: Any, time: Any, timezone: str) -> datetime | None:
    """Local ``yyyy-mm-dd`` + ``HH:MM``."""
    day_s, time_s = clean_str(day), clean_str(time)
    if not day_s or not time_s:
        return None
    return datetime.strptime(f"{day_s} {time_s[:5]}", "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo(timezone))
