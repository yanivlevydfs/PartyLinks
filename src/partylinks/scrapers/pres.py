"""PRES ticketing platform (pres.global) - Kupat Tel Aviv, Cameri, Habima, the Israeli Opera,
the Philharmonic, Beit Lessin, the Khan and other box offices on the same API.

Every configured host serves the same JSON API:

* Listing: ``GET /api/presentations/`` returns ``{presentations: [...]}``, every upcoming
  performance in one call (id, local ``dateTime``, feature id/name/categories, hall,
  venue/location name, city, ``soldout``, sale window). Some hosts keep old rows (Beit
  Lessin has 2021 VOD items), so performances that already started are dropped.
* Shows: ``GET /api/features/`` returns every show with its synopsis HTML and (on some
  hosts) ``imageData``, a CDN image URL. The ``featureIds`` filter is ignored by the
  server, so one call per host covers all shows. Hosts without ``imageData`` may serve
  artwork from a media path (``image_path``; Kupat's ``/api/features/<id>/media/...``).
* Prices: ``/api/presentations/<id>/ticketGroups`` returns price levels with their
  tickets (price, basePrice, serviceCharge). It is read once per show, from its next
  performance, and applied to all of that show's performances. Newer hosts answer GET;
  Kupat's older backend only POST (``ticket_groups_method``), with no seat hold.
* Buy page: ``https://<host>/order/<presentationId>``.

``source_event_id`` is ``<host key>:<presentation id>``. Kupat resells seats for other
venues, so the same performance can appear under two hosts; dedup merges them.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any, Literal
from urllib.parse import unquote

from pydantic import BaseModel, ConfigDict, Field

from partylinks.models import ScrapedEvent, TicketTier, TierStatus
from partylinks.parsing import (
    ParseError,
    clean_str,
    html_to_text,
    parse_iso,
    to_decimal,
    to_float,
    to_int,
    zone,
)
from partylinks.scrapers.base import (
    BaseScraper,
    SourceBlockedError,
    SourceSettings,
    gather_or_cancel,
)

# "ll=32.07%2C34.78" or "to=ll.32.09%2C34.79" in a Waze link.
_WAZE_LL = re.compile(r"ll[=.](-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)")
# Kupat flags retired category names with this prefix; the category text after it still applies.
_INACTIVE = re.compile(r"^\s*לא פעיל\s*")


class PresHost(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    url: str
    # Fallbacks for hosts whose rows only name the hall ("אולם 2") or omit the city.
    venue: str | None = None
    city: str | None = None
    address: str | None = None
    # Kupat's ``venueLocation`` is "region, city", not a street address.
    use_venue_location: bool = True
    ticket_groups_method: Literal["GET", "POST"] = "GET"
    # Artwork path for shows without ``imageData``, formatted with ``feature_id``.
    image_path: str | None = None


class PresSettings(SourceSettings):
    hosts: list[PresHost]
    presentations_path: str
    features_path: str
    ticket_groups_path: str
    event_page_path: str
    # Titles containing any of these are box-office items, not shows (tests, voucher charges).
    skip_titles: list[str] = Field(default_factory=list)


def _now(timezone: str) -> datetime:
    return datetime.now(zone(timezone))


class PresScraper(BaseScraper[PresSettings]):
    name = "pres"
    settings_model = PresSettings

    async def scrape(self) -> AsyncIterator[ScrapedEvent]:
        for host in self.settings.hosts:
            try:
                shows, features = await self._list_host(host)
            except SourceBlockedError:
                raise
            except Exception as exc:  # noqa: BLE001 - one host down never drops the others
                self.record_failure(f"{host.key} listing", exc)
                continue
            self.log.info(
                "%s: listed %d performances of %d shows",
                host.key,
                sum(map(len, shows.values())),
                len(shows),
            )
            async for events in self.stream_isolated(
                [(host, features.get(fid), rows) for fid, rows in shows.items()],
                self._show_events,
                label=lambda item: f"{item[0].key} show {item[2][0].get('featureId')}",
            ):
                for event in events:
                    yield event

    async def _show_events(self, item: tuple[PresHost, dict[str, Any] | None, list[dict[str, Any]]]) -> list[ScrapedEvent]:
        """Every upcoming performance of one show, priced from its next performance."""
        host, feature, performances = item
        payload = await self.optional(
            f"{host.key} prices {performances[0]['featureId']}",
            self._ticket_groups(host, performances[0]["id"]),
        )
        tiers = parse_ticket_groups(payload) if payload else []
        events = []
        for p in performances:
            try:
                events.append(parse_event(p, feature, tiers, host, self.settings, self.timezone))
            except Exception as exc:  # noqa: BLE001 - one bad row never drops its show
                self.record_failure(f"{host.key}:{p.get('id')}", exc)
        return events

    async def _list_host(self, host: PresHost) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
        s = self.settings
        listing, features = await gather_or_cancel(
            [
                self.get_json(host.url + s.presentations_path, expect=dict),
                self.optional(
                    f"{host.key} features",
                    self.get_json(host.url + s.features_path, expect=list),
                ),
            ]
        )
        shows = upcoming_shows(
            listing.get("presentations") or [],
            s.skip_titles,
            _now(self.timezone),
            self.timezone,
        )
        return shows, {str(f["id"]): f for f in features or [] if isinstance(f, dict) and f.get("id") is not None}

    async def _ticket_groups(self, host: PresHost, presentation_id: Any) -> Any:
        url = host.url + self.settings.ticket_groups_path.format(id=presentation_id)
        if host.ticket_groups_method == "POST":
            return await self.post_json(url, json={}, expect=dict)
        return await self.get_json(url, expect=dict)


def upcoming_shows(
    presentations: list[Any], skip_titles: list[str], now: datetime, timezone: str
) -> dict[str, list[dict[str, Any]]]:
    """Performances that have not started, grouped by show (feature id), soonest first."""
    shows: dict[str, list[dict[str, Any]]] = {}
    rows = [p for p in presentations if isinstance(p, dict) and p.get("id") is not None and p.get("featureId") is not None]
    for p in sorted(rows, key=lambda p: str(p.get("dateTime"))):
        start = parse_iso(p.get("dateTime"), timezone)
        title = str(p.get("featureName") or "")
        if start is None or start < now or any(skip in title for skip in skip_titles):
            continue
        shows.setdefault(str(p["featureId"]), []).append(p)
    return shows


def parse_ticket_groups(payload: dict[str, Any]) -> list[TicketTier]:
    """One tier per price level at its full price; concession tickets (seniors, students,
    card-holder vouchers) need proof or a code and are left out.

    ponytail: the show's next performance stands for all of them; per-date pricing would
    need one request per performance.
    """
    groups = payload.get("ticketGroups") or {}
    tiers = []
    for group in groups.values() if isinstance(groups, dict) else groups:
        tickets = [t for t in (group or {}).get("tickets") or [] if to_decimal(t.get("price")) is not None]
        if not tickets:
            continue
        full = max(tickets, key=lambda t: to_decimal(t["price"]))
        price = to_decimal(full["price"])
        base = to_decimal(full.get("basePrice"))
        fee = to_decimal(full.get("serviceCharge"))
        tiers.append(
            TicketTier(
                source_ticket_id=clean_str(group.get("id")),
                name=clean_str(group.get("name")) or clean_str(full.get("name")) or "כרטיס",
                price=price,
                original_price=base if base and base > price else None,
                service_fee=fee or None,
                is_free=price == 0,
            )
        )
    return tiers


def parse_event(
    p: dict[str, Any],
    feature: dict[str, Any] | None,
    tiers: list[TicketTier],
    host: PresHost,
    s: PresSettings,
    timezone: str,
) -> ScrapedEvent:
    presentation_id, feature_id = clean_str(p.get("id")), clean_str(p.get("featureId"))
    if not presentation_id or not feature_id:
        raise ParseError("presentation/feature id missing")
    feature = feature or {}
    sold_out = bool(p.get("soldout"))
    sale_start = parse_iso(p.get("ticketSaleStart"), timezone)
    status = (
        TierStatus.SOLD_OUT
        if sold_out
        else TierStatus.COMING_SOON
        if sale_start and sale_start > _now(timezone)
        else TierStatus.ON_SALE
    )
    tickets = [t.model_copy(update={"status": status, "sale_start": sale_start}) for t in tiers]
    priced = [t.price for t in tickets if t.price is not None]
    image = clean_str(feature.get("imageData")) or (
        host.url + host.image_path.format(feature_id=feature_id) if host.image_path else None
    )
    lat, lon = _waze_coordinates(p.get("locationWazeUrl"))
    categories = p.get("featureCategoryName") or feature.get("categoryName") or ""
    return ScrapedEvent(
        source=PresScraper.name,
        source_event_id=f"{host.key}:{presentation_id}",
        url=host.url + s.event_page_path.format(id=presentation_id),
        title=clean_str(p.get("featureName")) or clean_str(feature.get("name")) or "",
        description=html_to_text(feature.get("synopsis")),
        start_at=parse_iso(p.get("dateTime"), timezone),
        venue_name=_one_line(p.get("locationName")) or host.venue or _one_line(p.get("venueName")),
        address=(_one_line(p.get("venueLocation")) if host.use_venue_location else None) or host.address,
        city=_one_line(p.get("venueCity")) or host.city,
        latitude=lat,
        longitude=lon,
        source_genres=[c for raw in str(categories).split(",") if (c := _INACTIVE.sub("", raw).strip())],
        images=[image] if image else [],
        min_price=min(priced, default=None),
        max_price=max(priced, default=None),
        is_sold_out=sold_out,
        tickets=tickets,
        extra={
            "host": host.key,
            "feature_id": feature_id,
            "hall": _one_line(p.get("venueName")),
            "subtitle": clean_str(p.get("featureAdditionalName")),
            "duration_minutes": to_int(p.get("durationInMinutes")),
            "avail_ratio": to_float(p.get("availRatio")),
            "sale_end": clean_str(p.get("ticketSaleStop")),
        },
    )


def _one_line(value: Any) -> str | None:
    return clean_str(" ".join(str(value).split())) if value is not None else None


def _waze_coordinates(url: Any) -> tuple[float | None, float | None]:
    m = _WAZE_LL.search(unquote(str(url or "")))
    return (float(m.group(1)), float(m.group(2))) if m else (None, None)
